#!/usr/bin/env python3
"""AWS SNS webhook receiver for EC2 spot interruption + rebalance recommendation events.

Receives SNS notifications, optionally drains the affected
instance from ELB target groups, optionally triggers Jenkins
jobs, and posts a Slack notification.
"""
import argparse
import json
import sys
from typing import Any, Dict, List, Optional, TypedDict

import boto3
import requests
import yaml
from flask import Flask, Response, jsonify, make_response, request
from requests.auth import HTTPBasicAuth


class SlackConfig(TypedDict, total=False):
    """Slack section of the YAML config."""
    token: str
    channels: Dict[str, str]
    url: Optional[str]


class JenkinsConfig(TypedDict):
    """Jenkins section of the YAML config (optional top-level key)."""
    url: str
    username: str
    password: str


class JenkinsJob(TypedDict):
    """A single Jenkins job to trigger for a notification type."""
    endpoint_url: str
    regions: List[str]


class NotificationType(TypedDict, total=False):
    """One entry of the notification_types list in the YAML config."""
    name: str
    detail_type: str
    drain_target_groups: bool
    jenkins: Dict[str, List[JenkinsJob]]


class Config(TypedDict, total=False):
    """Top-level YAML config shape."""
    slack: SlackConfig
    jenkins: JenkinsConfig
    environments: Dict[str, str]
    notification_types: List[NotificationType]


def get_args() -> argparse.Namespace:
    """Parse and return command line arguments for local development."""
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


def load_config() -> Config:
    """Load the YAML config from `config.yml` in the current directory.

    Raises:
        FileNotFoundError: When `config.yml` is not present in CWD.
    """
    config_file = 'config.yml'
    try:
        with open(config_file, 'r') as stream:
            return yaml.safe_load(stream)
    except FileNotFoundError:
        raise FileNotFoundError(f'Config file {config_file} not found')


def validate_config(config: Optional[Config]) -> None:
    """Validate the structure of a loaded config.

    Checks that all required keys are present in `slack`, `jenkins`
    (if defined) and that `notification_types` exists.

    Raises:
        ValueError: When any required key is missing.
    """
    if config is None:
        raise ValueError('config is empty')

    if 'slack' not in config:
        raise ValueError("'slack' section not found in config")

    if 'token' not in config['slack']:
        raise ValueError("'token' not found in 'slack' section of config")

    if 'channels' not in config['slack']:
        raise ValueError("'channels' not found in 'slack' section of config")

    if 'jenkins' in config:
        if 'url' not in config['jenkins']:
            raise ValueError("'url' not found in 'jenkins' section of config")
        if 'username' not in config['jenkins']:
            raise ValueError("'username' not found in 'jenkins' section of config")
        if 'password' not in config['jenkins']:
            raise ValueError("'password' not found in 'jenkins' section of config")

    if 'notification_types' not in config:
        raise ValueError("'notification_types' not found in config, at least one notification type is required")


def get_slack_url(config: Config) -> str:
    """Resolve the Slack endpoint URL.

    Returns the URL from `slack.url` (concatenated with the token) when
    one is configured, otherwise falls back to the public Slack
    `chat.postMessage` endpoint.
    """
    if 'url' in config['slack']:
        return config['slack']['url'] + '/' + config['slack']['token']
    return 'https://slack.com/api/chat.postMessage'


def get_ec2_client(region: str) -> Any:
    """Return a boto3 EC2 client for the given region."""
    return boto3.client(
        'ec2',
        region_name=region
    )


def get_elb_client(region: str) -> Any:
    """Return a boto3 ELBv2 client for the given region."""
    return boto3.client(
        'elbv2',
        region_name=region
    )


def get_target_groups(region: str) -> List[Dict[str, Any]]:
    """Return all target groups visible to ELBv2 in the given region."""
    elb = get_elb_client(region)
    response = elb.describe_target_groups()

    return response['TargetGroups']


def get_elb_target_health(region: str, target_group_arn: str) -> Dict[str, str]:
    """Return a dict mapping instance-id to target-health state for a target group."""
    elb = get_elb_client(region)
    health: Dict[str, str] = {}

    response = elb.describe_target_health(
        TargetGroupArn=target_group_arn,
    )

    for target in response['TargetHealthDescriptions']:
        instance_id = target['Target']['Id']
        instance_state = target['TargetHealth']['State']

        health[instance_id] = instance_state

    return health


def drain_instance_from_elb_target_groups(region: str, instance_id: str) -> None:
    """Deregister the instance from every target group it is currently a member of."""
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


def get_ec2_resource(region: str) -> Any:
    """Return a boto3 EC2 resource for the given region."""
    return boto3.resource(
        'ec2',
        region_name=region
    )


def get_ec2_instance(region: str, instance_id: str) -> Optional[Any]:
    """Return a boto3 EC2.Instance for the given id, or None on lookup failure."""
    try:
        ec2 = get_ec2_resource(region)
        return ec2.Instance(instance_id)
    except Exception:
        return None


def get_spot_request_for_instance_id(region: str, instance_id: str) -> Optional[Dict[str, Any]]:
    """Return the spot-instance-request dict that owns the given instance, or None."""
    ec2 = get_ec2_client(region)
    response = ec2.describe_spot_instance_requests()

    for spot_request in response['SpotInstanceRequests']:
        if spot_request['InstanceId'] == instance_id:
            return spot_request

    return None


def get_jenkins_crumb(config: Config) -> str:
    """Fetch a CSRF crumb from Jenkins for subsequent authenticated POSTs."""
    jenkins_url = config['jenkins']['url']

    crumb_resp = requests.get(
        f'{jenkins_url}/crumbIssuer/api/json',
        auth=HTTPBasicAuth(config['jenkins']['username'], config['jenkins']['password'])
    )

    return crumb_resp.json()['crumb']


def run_jenkins_job(
    config: Config,
    region: str,
    jenkins_job_endpoint: str,
    instance_id: str,
    ec2_instance: Optional[Any],
) -> None:
    """Trigger a parameterised Jenkins job for the affected instance.

    Substitutes the `{{ SERVER_IP }}`, `{{ INSTANCE_ID }}` and
    `{{ ENVIRONMENT }}` placeholders in the endpoint URL before
    issuing the POST. Silently returns when `ec2_instance` is None
    (a lookup failure should not break the rest of the handler).
    """
    jenkins_url = config['jenkins']['url']
    jenkins_job_url = f'{jenkins_url}/job/{jenkins_job_endpoint}'

    if ec2_instance is None:
        return

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
            headers={"Jenkins-Crumb": get_jenkins_crumb(config)}
        )

        if job_resp.status_code != 201:
            raise Exception(f'Failed to invoke Jenkins job: {jenkins_job_url}')

        print(f'Jenkins job invoked successfully: {jenkins_job_url}')
    except Exception as e:
        print(f'Unable to invoke Jenkins job: {e}')


def send_slack_notification(config: Config, sns_message: Dict[str, Any]) -> Response:
    """Format and post the SNS message to the configured Slack channel.

    For spot-interruption messages, fetches the matching spot-request
    record and appends the termination reason code + message. Returns
    a Flask `Response` ready to be returned from a route handler.
    """
    slack_url = get_slack_url(config)
    slack_token = config['slack']['token']
    slack_channels = config['slack']['channels']

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


def create_app(config: Optional[Config] = None) -> Flask:
    """Build and return the Flask app.

    When `config` is None, attempts to `load_config()` and
    `validate_config()` automatically. Tests should pass an
    explicit `config` to bypass on-disk loading.
    """
    if config is None:
        config = load_config()
        validate_config(config)

    app = Flask(__name__)

    @app.errorhandler(404)
    def not_found(error: Exception) -> Response:
        """JSON 404 handler."""
        return make_response(jsonify(
            {
                'status': 'error',
                'msg': f'{request.url} not found',
                'detail': str(error)
            }
        ), 404)

    @app.errorhandler(500)
    def internal_server_error(error: Exception) -> Response:
        """JSON 500 handler."""
        return make_response(jsonify(
            {
                'status': 'error',
                'msg': 'Internal Server Error',
                'detail': str(error)
            }
        ), 500)

    @app.route('/', methods=['GET'])
    def ping() -> Response:
        """Health check endpoint."""
        return make_response(jsonify(
            {
                'status': 'ok'
            }
        ), 200)

    @app.route('/', methods=['POST'])
    def webhook_handler() -> Response:
        """Receive an SNS notification and dispatch the configured side effects."""
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
                            run_jenkins_job(config, region, jenkins_job['endpoint_url'], instance_id, instance)

        return send_slack_notification(config, sns_message)

    return app


try:
    config: Optional[Config] = load_config()
    validate_config(config)
    app: Optional[Flask] = create_app(config)
except Exception as e:  # pragma: no cover
    print(f'WARNING: Failed to load config: {e}')
    config = None
    app = None


def main() -> None:
    """Entry point for local development (`python3 webhook.py`)."""
    args = get_args()
    if app is None:
        sys.exit(1)
    app.run(
        host=args.host,
        port=args.port
    )


if __name__ == '__main__':
    main()
