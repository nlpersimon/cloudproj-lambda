from boto3 import client
import uuid
import os
import json
from linebot import LineBotApi
from linebot.models import TextSendMessage
from extractor import Extractor
import requests



extractor = Extractor()
ddb = client('dynamodb', region_name='us-east-1')
sns = client('sns', region_name='us-east-1')
line_bot_api = LineBotApi(os.environ['LINE_ACCESS_TOKEN'])


def lambda_handler(event, context):
    for record in event['Records']:
        body = json.loads(record['body'])

        topic = extractor.extract_topic(body['texts'])
        print(f'Topic: {topic}')

        topic_data = create_topic_data(topic, body)

        response = write_to_ddb(topic_data)
        print(response)

        sns.publish(
            TopicArn=os.environ['SNS_TOPIC_ARN'],
            Subject='問題討論群有新問題啦，快來看看！',
            Message=f'{body["username"]}:\n{topic}'
        )

        publish_to_line_group(topic_data)

        response = publish_to_frontend(topic_data)
        print('frontend response:', response)
        if response.status_code == 200:
            print('frontend response content:', response.text)

    return

def create_topic_data(topic, body):
    topic_id = uuid.uuid4().hex
    topic_data = {
        'topic_id': topic_id,
        'username': body['username'],
        'date': body['date'],
        'time': body['time'],
        'topic': topic
    }
    return topic_data

def write_to_ddb(topic_data):
    item = {
        'topic_id': {'S': topic_data['topic_id']},
        'username': {'S': topic_data['username']},
        'date': {'S': topic_data['date']},
        'time': {'S': topic_data['time']},
        'topic': {'S': topic_data['topic']}
    }
    response = ddb.put_item(
        TableName=os.environ['TABLE_NAME'],
        Item=item
    )
    return response

def publish_to_line_group(topic_data):
    username, topic = topic_data['username'], topic_data['topic']
    line_bot_api.push_message(
        os.environ['GROUP_ID'],
        TextSendMessage(text=f'@{username} 剛剛發佈了新問題，快來看看吧！\n{topic}')
    )
    return

def publish_to_frontend(topic_data):
    url = 'https://wiq2ve4q31.execute-api.us-east-1.amazonaws.com/devx/post'
    response = requests.post(
        url,
        json={
            'id': topic_data['topic_id'],
            'title': topic_data['topic'],
            'timestamp': f'{topic_data["date"]} {topic_data["time"]}'
        }
    )
    return response
