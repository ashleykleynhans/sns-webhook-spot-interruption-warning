#!/usr/bin/env python3
import json
import sys
import argparse
import yaml
import requests
import boto3
from flask import Flask, request, jsonify, make_response


def get_args():
    parser = argparse.ArgumentParser(
        description='AWS SNS Webhook Receiver to Send Slack Notifications'
    )

    parser.add_argument(
        '-p', '--port',
        help='Port to listen on',
        type=int,
        default=8090
    )

    parser.add_argument(
        '-H', '--host',
        help='Host to bind to',
        default='0.0.0.0'
    )

    return parser.parse_args()


def load_config():
    try:
        config_file = 'config.yml'

        with open(config_file, 'r') as stream:
            return yaml.safe_load(stream)
    except FileNotFoundError:
        print(f'ERROR: Config file {config_file} not found!')
        sys.exit()


config = load_config()

if 'slack' not in config:
    print("'slack' section not found in config")
    sys.exit(1)

if 'token' not in config['slack']:
    print("'token' not found in 'slack' section of config")
    sys.exit(1)

if 'url' in config['slack']:
    slack_url = config['slack']['url'] + '/' + config['slack']['token']
else:
    slack_url = 'https://slack.com/api/chat.postMessage'

slack_token = config['slack']['token']
slack_channel = config['slack']['channel']
app = Flask(__name__)


def get_elb_client(region):
    return boto3.client(
        'elbv2',
        region_name=region
    )


def get_target_groups(region):
    elb = get_elb_client(region)
    response = elb.describe_target_groups()

    return response['TargetGroups']


def get_elb_target_health(region, target_group_arn):
    elb = get_elb_client(region)
    health = {}

    response = elb.describe_target_health(
        TargetGroupArn=target_group_arn,
    )

    for target in response['TargetHealthDescriptions']:
        instance_id = target['Target']['Id']
        instance_state = target['TargetHealth']['State']

        health[instance_id] = instance_state

    return health


def drain_instance_from_elb_target_groups(region, instance_id):
    elb = get_elb_client(region)
    target_groups = get_target_groups(region)

    for target_group in target_groups:
        target_group_health = get_elb_target_health(region, target_group['TargetGroupArn'])

        if instance_id in target_group_health:
            print(f"Draining instance {instance_id} from target group {target_group['TargetGroupName']}")

            elb.deregister_targets(
                TargetGroupArn=target_group['TargetGroupArn'],
                Targets=[
                    {
                        'Id': instance_id
                    },
                ]
            )


def send_slack_notification(sns_message):
    message = ''

    for msg_item in sns_message.keys():
        message += f'{msg_item}: {sns_message[msg_item]}\n'

    slack_payload = {
        'attachments': [
            {
                'title': sns_message['detail-type'],
                'text': message,
                'fallback': message,
                'color': 'danger'
            }
        ],
        'channel': f'#{slack_channel}'
    }

    response = requests.post(
        url=slack_url,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {slack_token}'
        },
        json=slack_payload
    )

    slack_response = response.json()

    if response.status_code != 200:
        return make_response(jsonify(
            {
                'status': 'error',
                'msg': 'Failed to send Slack notification',
                'detail': slack_response
            }
        ), 500)

    return jsonify(slack_response)


@app.errorhandler(404)
def not_found(error):
    return make_response(jsonify(
        {
            'status': 'error',
            'msg': f'{request.url} not found',
            'detail': str(error)
        }
    ), 404)


@app.errorhandler(500)
def internal_server_error(error):
    return make_response(jsonify(
        {
            'status': 'error',
            'msg': 'Internal Server Error',
            'detail': str(error)
        }
    ), 500)


@app.route('/', methods=['GET'])
def ping():
    return make_response(jsonify(
        {
            'status': 'ok'
        }
    ), 200)


@app.route(f'/', methods=['POST'])
def webhook_handler():
    sns_payload = json.loads(request.data.decode('utf-8'))
    sns_message = json.loads(sns_payload['Message'])
    drain_instance_from_elb_target_groups(sns_message['region'], sns_message['detail']['instance-id'])
    return send_slack_notification(sns_message)


if __name__ == '__main__':
    args = get_args()

    app.run(
        host=args.host,
        port=args.port
    )
