#!/usr/bin/env python3
import json
import sys
import argparse
import yaml
import requests
import boto3
from requests.auth import HTTPBasicAuth
from flask import Flask, request, jsonify, make_response
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS


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

if 'channels' not in config['slack']:
    print("'channels' not found in 'slack' section of config")
    sys.exit(1)

if 'url' in config['slack']:
    slack_url = config['slack']['url'] + '/' + config['slack']['token']
else:
    slack_url = 'https://slack.com/api/chat.postMessage'

if 'jenkins' in config:
    if 'url' not in config['jenkins']:
        print("'url' not found in 'jenkins' section of config")
        sys.exit(1)

    if 'username' not in config['jenkins']:
        print("'username' not found in 'jenkins' section of config")
        sys.exit(1)

    if 'password' not in config['jenkins']:
        print("'password' not found in 'jenkins' section of config")
        sys.exit(1)

if 'notification_types' not in config:
    print("'notification_types' not found config, at least one notification type is required")
    sys.exit(1)

slack_token = config['slack']['token']
slack_channels = config['slack']['channels']
app = Flask(__name__)


def get_ec2_client(region):
    return boto3.client(
        'ec2',
        region_name=region
    )


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


def get_ec2_resource(region):
    return boto3.resource(
        'ec2',
        region_name=region
    )


def get_ec2_instance(region, instance_id):
    try:
        ec2 = get_ec2_resource(region)
        return ec2.Instance(instance_id)
    except Exception as e:
        return None


def get_spot_request_for_instance_id(region, instance_id):
    ec2 = get_ec2_client(region)
    response = ec2.describe_spot_instance_requests()

    for spot_request in response['SpotInstanceRequests']:
        if spot_request['InstanceId'] == instance_id:
            return spot_request


def get_jenkins_crumb():
    jenkins_url = config['jenkins']['url']

    crumb_resp = requests.get(
        f'{jenkins_url}/crumbIssuer/api/json',
        auth=HTTPBasicAuth(config['jenkins']['username'], config['jenkins']['password'])
    )

    crumb_resp = crumb_resp.json()

    return crumb_resp['crumb']


def run_jenkins_job(region, jenkins_job_endpoint, instance_id, ec2_instance):
    jenkins_url = config['jenkins']['url']
    jenkins_job_url = f'{jenkins_url}/job/{jenkins_job_endpoint}'

    if ec2_instance is not None:
        try:
            if '{{ SERVER_IP }}' in jenkins_job_url:
                jenkins_job_url = jenkins_job_url.replace('{{ SERVER_IP }}', ec2_instance.private_ip_address)

            if '{{ INSTANCE_ID }}' in jenkins_job_url:
                jenkins_job_url = jenkins_job_url.replace('{{ INSTANCE_ID }}', instance_id)

            if '{{ ENVIRONMENT }}' in jenkins_job_url and 'environments' in config:
                if region in config['environments']:
                    environment = config['environments'][region]
                    jenkins_job_url = jenkins_job_url.replace('{{ ENVIRONMENT }}', environment)
                else:
                    print(f'{{ ENVIRONMENT }} variable was not substituted in {jenkins_job_url}')
                    return

            print(f'Jenkins job URL: {jenkins_job_url}')

            job_resp = requests.post(
                jenkins_job_url,
                auth=HTTPBasicAuth(config['jenkins']['username'], config['jenkins']['password']),
                headers={"Jenkins-Crumb": get_jenkins_crumb()}
            )

            if job_resp.status_code != 201:
                raise Exception(f'Failed to invoke Jenkins job: {jenkins_job_url}')
            else:
                print(f'Jenkins job invoked successfully: {jenkins_job_url}')
        except Exception as e:
            print(f'Unable to invoke Jenkins job: {e}')


def send_slack_notification(sns_message):
    message = ''

    for msg_item in sns_message.keys():
        message += f'**{msg_item}**: {sns_message[msg_item]}\n'

    if sns_message['detail-type'] == 'EC2 Spot Instance Interruption Warning':
        spot_request = get_spot_request_for_instance_id(
            sns_message['region'],
            sns_message['detail']['instance-id']
        )

        if spot_request is not None:
            reason_code = spot_request['Status']['Code']
            reason_message = spot_request['Status']['Message']
            message += f'**reason_code**: {reason_code}\n'
            message += f'**reason**: {reason_message}'

    slack_channel = slack_channels[sns_message['region']]

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


def influxdb_log(message, ec2_instance):
    try:
        if 'influxdb' not in config:
            return

        if 'detail-type' in message and message['detail-type'] == 'EC2 Spot Instance Interruption Warning':
            if message['region'] in config['environments']:
                environment = config['environments'][message['region']]
                influxdb_config = config['influxdb'][environment]
                asg_name = ''

                for tag in ec2_instance.tags:
                    if tag['Key'] == 'aws:autoscaling:groupName':
                        asg_name = tag['Value']


                print('Logging to InfluxDB')

                client = InfluxDBClient(
                    url=influxdb_config['url'],
                    token=influxdb_config['token'],
                    org=influxdb_config['org']
                )

                point = Point('spot_termination') \
                    .tag('availability_zone', ec2_instance.placement['AvailabilityZone']) \
                    .tag('autoscaling_group', asg_name) \
                    .field('count', 1)

                write_api = client.write_api(write_options=SYNCHRONOUS)
                write_api.write(
                    influxdb_config['bucket'],
                    influxdb_config['org'],
                    point,
                    write_precision=WritePrecision.S
                )

                client.close()
    except Exception as e:
        print(f'Logging to InfluxDB failed: {e}')


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
    region = sns_message['region']
    instance_id = sns_message['detail']['instance-id']
    instance = get_ec2_instance(region, instance_id)

    for notification in config['notification_types']:
        if sns_message['detail-type'] == notification['detail_type']:
            if 'drain_target_groups' in notification and notification['drain_target_groups']:
                drain_instance_from_elb_target_groups(region, instance_id)

            if 'jenkins' in notification and 'jobs' in notification['jenkins']:
                for jenkins_job in notification['jenkins']['jobs']:
                    if region in jenkins_job['regions']:
                        run_jenkins_job(region, jenkins_job['endpoint_url'], instance_id, instance)

    influxdb_log(sns_message, instance)

    return send_slack_notification(sns_message)


if __name__ == '__main__':
    args = get_args()

    app.run(
        host=args.host,
        port=args.port
    )
