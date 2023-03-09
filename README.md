# SNS Webhook receiver to receive EC2 Instance Spot Termination Warnings/Rebalance Recommendations

This is an AWS SNS Webhook Receiver that receives SNS notifications
for EC2 Spot Instance Rebalance Recommendations and EC2 Spot
Interruption warnings and send notifications to Slack.

It can also optionally drain the affected instances from Load Balancer
Target Groups, trigger Jenkins jobs to perform specific actions
(for example removing the affected instance from monitoring systems),
and log to InfluxDB.

[![Python Version: 3.9](
https://img.shields.io/badge/Python%20application-v3.9-blue
)](https://www.python.org/downloads/release/python-3913/)
[![License: GPL 3.0](
https://img.shields.io/github/license/ashleykleynhans/sns-webhook-spot-interruption-warning
)](https://opensource.org/licenses/GPL-3.0)

## Prerequisites

1. Install [ngrok](https://ngrok.com/).
```bash
brew install ngrok
```
2. Ensure your System Python3 version is 3.9, but greater than 3.9.1.
```bash
python3 -V
```
3. If your System Python is not 3.9:
```bash
brew install python@3.9
brew link python@3.9
```
4. If your System Python is 3.9 but not greater than 3.9.1:
```bash
brew update
brew upgrade python@3.9
```
5. [Create a new Slack App](https://api.slack.com/start).
6. Create your Slack channel where you want to receive your SNS notifications.
7. Configure SNS to send notifications to that channel.
8. Create a configuration file called `config.yml` in the same directory
   as the webhook script that looks like this:
```yml
---
slack:
  token: "<SLACK_TOKEN>"
  channels:
    us-east-1: aws-alerts-prod
    us-east-2: aws-alerts-test

jenkins:
   url: http://jenkins.example.com
   username: "<JENKINS_USER>"
   password: "<JENKINS_PASS>"

influxdb:
   prod:
      url: http://influxdb-prod.example.com:8086
      token: "<INFLUXDB_TOKEN>"
      org: "<INFLUXDB_ORG>"
      bucket: "<INFLUXDB_BUCKET>"
   test:
      url: http://influxdb-test.example.com:8086
      token: "<INFLUXDB_TOKEN>"
      org: "<INFLUXDB_ORG>"
      bucket: "<INFLUXDB_BUCKET>"

environments:
   us-east-1: prod
   us-east-2: test

notification_types:
   - name: ec2-spot-interruption
     detail_type: "EC2 Spot Instance Interruption Warning"
     drain_target_groups: true
     jenkins:
        jobs:
           - endpoint_url: remove-host-from-checkmk-prod/buildWithParameters?SERVER_IP={{ SERVER_IP }}
             regions:
                - us-east-1

   - name: ec2-rebalance-recommendation
     detail_type: "EC2 Instance Rebalance Recommendation"
     drain_target_groups: false
     jenkins:
        jobs:
           - endpoint_url: replace-ec2-instance-{{ ENVIRONMENT }}/buildWithParameters?INSTANCE_ID={{ INSTANCE_ID }}
             regions:
                - us-east-1
                - us-east-2

```

## AWS SNS Configuration

TODO

## Testing your Webhook

1. Run the webhook receiver from your terminal.
```bash
python3 webhook.py
```
2. Open a new terminal window and use [ngrok](https://ngrok.com/) to create
a URL that is publically accessible through the internet by creating a tunnel
to the webhook receiver that is running on your local machine.
```bash
ngrok http 8090
```
3. Note that the ngrok URL will change if you stop ngrok and run it again,
   so keep it running in a separate terminal window, otherwise you will not
   be able to test your webhook successfully.
4. Update your SNS webhook configuration to the URL that is displayed
while ngrok is running **(be sure to use the https one)**.
5. Trigger an SNS event to trigger the notification webhook.
6. Check your Slack channel that you created for your SNS notifications.

## Deploy to AWS Lambda

1. Create a Python 3.9 Virtual Environment:
```bash
python3 -m venv venv/py3.9
source venv/py3.9/bin/activate
```
2. Upgrade pip.
```bash
python3 -m pip install --upgrade pip
```
3. Install the Python dependencies that are required by the Webhook receiver:
```bash
pip3 install -r requirements.txt
```
4. Create a file called `zappa_settings.json` and insert the JSON content below
to configure your AWS Lambda deployment:
```json
{
    "sns": {
        "app_function": "webhook.app",
        "aws_region": "us-east-1",
        "lambda_description": "Webhook to handle EC2 Spot Instance Interruption Warnings",
        "profile_name": "default",
        "project_name": "spot-interruption-warning",
        "runtime": "python3.9",
        "s3_bucket": "sns-spot-interruptions",
        "tags": {
            "service": "spot-interruption-warning"
        },
       "extra_permissions": [
          {
             "Effect": "Allow",
             "Action": "elasticloadbalancing:*",
             "Resource": "*"
          }
       ]
    }
}
```
5. Use [Zappa](https://github.com/Zappa/Zappa) to deploy your Webhook
to AWS Lambda (this is installed as part of the dependencies above):
```bash
zappa deploy
```
6. Take note of the URL that is returned by the `zappa deploy` command,
eg. `https://1d602d00.execute-api.us-east-1.amazonaws.com/sns`
   (obviously use your own and don't copy and paste this one, or your
Webhook will not work).

**NOTE:** If you get the following error when running the `zappa deploy` command:

<pre>
botocore.exceptions.ClientError:
An error occurred (IllegalLocationConstraintException) when calling
the CreateBucket operation: The unspecified location constraint
is incompatible for the region specific endpoint this request was sent to.
</pre>

This error usually means that your S3 bucket name is not unique, and that you
should change it to something different, since the S3 bucket names are not
namespaced and are global for everyone.

7. Check the status of the API Gateway URL that was created by zappa:
```bash
zappa status
```
8. Test your webhook by making a curl request to the URL that was returned
by `zappa deploy`:
```
curl https://1d602d00.execute-api.us-east-1.amazonaws.com/sns
```
You should expect the following response:
```json
{"status":"ok"}
```
9. Update your Webhook URL in SNS to the one returned by the
`zappa deploy` command.
10. You can view your logs by running:
```bash
zappa tail
```
