#!/usr/bin/env python3
import requests
import json

PAYLOAD =  {
    "Type": "Notification",
    "MessageId": "01ad289b-f5c4-5919-883e-7348de026b72",
    "TopicArn": "arn:aws:sns:us-east-1:603535515465:autoscaling-prod",
    "Message": "{\"version\": \"0\", \"id\": \"1e5527d7-bb36-4607-3370-4164db56a40e\", \"detail-type\": \"EC2 Spot Instance Interruption Warning\", \"source\": \"aws.ec2\", \"account\": \"123456789012\", \"time\": \"1970-01-01T00:00:00Z\", \"region\": \"us-east-2\", \"resources\": [\"arn:aws:ec2:us-east-1b:instance/i-0b662ef9931388ba0\"], \"detail\": {\"instance-id\": \"i-0dd7ee85bccdacd76\", \"instance-action\": \"terminate\"}}",
    "Timestamp": "2022-10-26T12:35:18.454Z",
    "SignatureVersion": "1",
    "Signature": "2pWm1LmNQ1o0IKoLa9gLV/sJMc1vhXAOagS4e2iD7afsee6oxan3OjzUavdRSj0A9TARAhMZbgdvyY4TBABYAYgbij6IEzEobezLtmDSfoQKMMzYXu0qbm3Ttt7jllnDfqjz6NMtNXUsgxunMKVDMfMaPmTbvFno1svq8VtVr3HdOhswhrWA9ab5eniO+QVtwUCoevCpJQiQ2VKXTlCiNmW8uk7MwrYGcmvESGgmzzXvCjLPy1D2l9ORYGknomy05kGyRizGrwtO3tQqOcMdN4ozWGAxEo/+vMkdgRhlBruCyr50qAcxen/mZSiSEo2zO6iLvuN4hiyo6wX9q3b1RA==",
    "SigningCertURL": "https://sns.us-east-2.amazonaws.com/SimpleNotificationService-56e67fcb41f6fec09b0196692625d385.pem",
    "UnsubscribeURL": "https://sns.us-east-2.amazonaws.com/?Action=Unsubscribe&SubscriptionArn=arn:aws:sns:us-east-2:603535515465:autoscaling-test:0fbe4de0-d57a-452b-b804-96bce8c00407"
}

r = requests.post('http://127.0.0.1:8090/', json=PAYLOAD)
print(json.dumps(r.json(), indent=4, default=str))
