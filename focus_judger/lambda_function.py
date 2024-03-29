from boto3 import client
from datetime import datetime
import pytz
import os
from linebot import LineBotApi
from linebot.models import TextSendMessage
import json
import requests
import uuid
from numpy.random import normal
from numpy import clip



UPPER_BOUND = 1

rekog = client('rekognition', region_name='us-east-1')
ddb = client('dynamodb', region_name='us-east-1')
line_bot_api = LineBotApi(os.environ['LINE_ACCESS_TOKEN'])

def lambda_handler(event, context):
    """
    Args:
        event: an API gateway event
    """
    print('event:', event)
    params = json.loads(event['body'])

    # determine if there are any faces in the photo
    has_face = check_s3_object_has_face(
        params['photo']['bucket_name'],
        params['photo']['key']
    )
    
    # create the attributes of the table item
    username = params['username'].replace('%40', '@')
    event_date, event_time = convert_event_timezone(
        event['requestContext']['time']
    ).split(' ')
    print(f'{username} - {event_date} {event_time} - has face: {has_face}')

    response = write_to_ddb(username, event_date, event_time, has_face)

    screenshot_status = classify_screenshot(
        params['screenshot']['bucket_name'],
        params['screenshot']['key']
    )
    print(f'{username} - {event_date} {event_time} - screenshot status: {screenshot_status}')

    warning = update_user_absense_status(username, has_face, screenshot_status)
    if warning:
        publish_canned_message(username)
        response = signal_iot()
        print('MQTT response:', response)

    response = publish_to_frontend(
        username,
        has_face,
        screenshot_status,
        warning,
        event_date,
        event_time
    )
    print('frontend response:', response)
    if response.status_code == 200:
        print('frontend response content:', response.text)
    return

def check_s3_object_has_face(bucket_name, key):
    key = key.replace('%40', '@')
    response = rekog.detect_faces(
        Image={
            'S3Object': {
                'Bucket': bucket_name,
                'Name': key,
            }
        }
    )
    # there is at least one face in the photo
    # if and only if response['FaceDetails] is not empty
    return bool(response['FaceDetails'])

def convert_event_timezone(dt_str):
    orig_dt = datetime.strptime(
        dt_str.split(' ')[0],
        '%d/%b/%Y:%H:%M:%S'
    ).replace(tzinfo=pytz.UTC)
    taipei_dt = orig_dt.astimezone(pytz.timezone('Asia/Taipei'))
    taipei_dt_str = taipei_dt.strftime('%Y-%m-%d %H:%M:%S')
    return taipei_dt_str

def write_to_ddb(username, event_date, event_time, has_face):
    response = ddb.put_item(
        TableName=os.environ['TABLE_NAME'],
        Item={
            'username': {'S': username},
            'date': {'S': event_date},
            'time': {'S': event_time},
            'focus': {'N': str(int(has_face))}
        }
    )
    return response

def classify_screenshot(bucket_name, key):
    url = 'https://k7sa8xr0sd.execute-api.us-east-1.amazonaws.com/default/myfinalfunc'
    res = requests.post(
        url,
        json={
            'bucket_name': bucket_name,
            'key': key
        }
    )
    lazy = int(res.json()['screenshot_status'][0]['Name'])
    return lazy

def publish_to_frontend(username, has_face, screenshot_status, warning, date, time):
    userinfo_id = uuid.uuid4().hex
    is_focus = has_face and (screenshot_status == 0)
    focus_score = compute_focus_score(is_focus)
    params = {
        'id': userinfo_id,
        'name': username,
        'user_status_info': str(int(has_face)),
        'focus_score': str(round(focus_score, 2)),
        'focusing': str(int(not warning)),
        'screening_status': 'working' if screenshot_status == 0 else 'lazy',
        'timestamp': f'{date} {time}'
    }
    response = requests.post(
        'https://wiq2ve4q31.execute-api.us-east-1.amazonaws.com/devx/user',
        json=params
    )
    return response

def compute_focus_score(is_focus):
    mean = 0.75 if is_focus else 0.25
    lower = 0.5 if is_focus else 0
    higher = 1 if is_focus else 0.5
    score = clip(
        normal(mean, 0.1, 1)[0],
        lower,
        higher
    )
    return score

def update_user_absense_status(username, has_face, screenshot_status):
    if has_face and screenshot_status == 0:
        return False
    item = get_absense_item(username)
    if item is None:
        response = update_absense_item(username, 0)
        return False
    absense_cnt = int(item['absense_count']['N'])
    if absense_cnt >= UPPER_BOUND:
        response = update_absense_item(username, 0)
        return True
    else:
        response = update_absense_item(username, absense_cnt + 1)
        return False

def get_absense_item(username):
    response = ddb.get_item(
        TableName=os.environ['ABSENSE_TABLE'],
        Key={
            'username': {
                'S': username
            }
        }
    )
    item = response.get('Item', None)
    return item

def update_absense_item(username, absense_cnt):
    response = ddb.put_item(
        TableName=os.environ['ABSENSE_TABLE'],
        Item={
            'username': {'S': username},
            'absense_count': {'N': str(absense_cnt)}
        }
    )
    return response

def publish_canned_message(username):
    line_bot_api.push_message(
        os.environ['GROUP_ID'],
        TextSendMessage(text=f'@{username} 已經連續分心{UPPER_BOUND + 1}次了，還敢混啊！')
    )
    pass

def signal_iot():
    res = requests.get('https://4rvc6h64d7.execute-api.us-east-1.amazonaws.com/default/mqttfunc')
    return res