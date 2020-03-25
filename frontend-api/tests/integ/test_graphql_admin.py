import datetime
import os
import random
import string
import uuid
import pytest
import requests
import boto3
from fixtures import get_order, get_product # pylint: disable=import-error,no-name-in-module
from helpers import get_parameter # pylint: disable=import-error,no-name-in-module


@pytest.fixture(scope="module")
def warehouse_table_name():
    """
    Warehouse DynamoDB table name
    """

    return get_parameter("/ecommerce/{Environment}/warehouse/table/name")


@pytest.fixture(scope="module")
def delivery_table_name():
    """
    Delivery DynamoDB table name
    """

    return get_parameter("/ecommerce/{Environment}/delivery/table/name")


@pytest.fixture(scope="module")
def user_pool_id():
    """
    Cognito User Pool ID
    """

    return get_parameter("/ecommerce/{Environment}/users/user-pool/id")


@pytest.fixture
def api_id():
    """
    Frontend GraphQL API ID
    """

    return get_parameter("/ecommerce/{Environment}/frontend-api/api/id")


@pytest.fixture
def api_url():
    """
    Frontend GraphQL API URL
    """

    return get_parameter("/ecommerce/{Environment}/frontend-api/api/url")


@pytest.fixture(scope="module")
def password():
    """
    Generate a unique password for the user
    """

    return "".join(
        random.choices(string.ascii_uppercase, k=10) +
        random.choices(string.ascii_lowercase, k=10) +
        random.choices(string.digits, k=5) +
        random.choices(string.punctuation, k=3)
    )


@pytest.fixture(scope="module")
def email():
    """
    Generate a unique email address for the user
    """

    return "".join(random.choices(string.ascii_lowercase, k=20))+"@example.local"


@pytest.fixture(scope="module")
def client_id(user_pool_id):
    """
    Return a user pool client
    """

    cognito = boto3.client("cognito-idp")

    # Create a Cognito User Pool Client
    response = cognito.create_user_pool_client(
        UserPoolId=user_pool_id,
        ClientName="ecommerce-{}-frontend-api-test".format(os.environ["ECOM_ENVIRONMENT"]),
        GenerateSecret=False,
        ExplicitAuthFlows=["ADMIN_NO_SRP_AUTH"]
    )

    # Return the client ID
    client_id = response["UserPoolClient"]["ClientId"]
    yield client_id

    # Delete the client
    cognito.delete_user_pool_client(
        UserPoolId=user_pool_id,
        ClientId=client_id
    )


@pytest.fixture(scope="module")
def user_id(user_pool_id, email, password):
    """
    User ID generated by Cognito
    """

    cognito = boto3.client("cognito-idp")

    # Create a Cognito user
    response = cognito.admin_create_user(
        UserPoolId=user_pool_id,
        Username=email,
        UserAttributes=[{
            "Name": "email",
            "Value": email
        }]
    )
    user_id = response["User"]["Username"]
    cognito.admin_set_user_password(
        UserPoolId=user_pool_id,
        Username=user_id,
        Password=password,
        Permanent=True
    )

    cognito.admin_add_user_to_group(
        UserPoolId=user_pool_id,
        Username=user_id,
        GroupName="admin"
    )

    # Return the user ID
    yield user_id

    # Delete the user
    cognito.admin_delete_user(
        UserPoolId=user_pool_id,
        Username=user_id
    )


@pytest.fixture(scope="module")
def jwt_token(user_pool_id, user_id, client_id, email, password):
    """
    Returns a JWT token for API Gateway
    """

    cognito = boto3.client("cognito-idp")

    response = cognito.admin_initiate_auth(
        UserPoolId=user_pool_id,
        ClientId=client_id,
        AuthFlow="ADMIN_NO_SRP_AUTH",
        AuthParameters={
            "USERNAME": email,
            "PASSWORD": password
        }
    )

    return response["AuthenticationResult"]["IdToken"]


def test_get_new_packaging_request_ids(jwt_token, api_url, warehouse_table_name):
    """
    Test getNewPackagingRequestIds
    """

    order_metadata = {
        "orderId": str(uuid.uuid4()),
        "productId": "__metadata",
        "modifiedDate": datetime.datetime.now().isoformat(),
        "newDate": datetime.datetime.now().isoformat(),
        "status": "NEW"
    }

    print(order_metadata)

    # Seed the database
    table = boto3.resource("dynamodb").Table(warehouse_table_name) # pylint: disable=no-member
    table.put_item(Item=order_metadata)

    # Make requests
    headers = {"Authorization": jwt_token}
    def get_ids(next_token=None):
        if next_token:
            req_data = {
                "query": """
                query ($nextToken: String!) {
                    getNewPackagingRequestIds(nextToken: $nextToken) {
                        nextToken
                        packagingRequestIds
                    }
                }
                """,
                "variables": {
                    "nextToken": next_token
                }
            }
        else:
            req_data = {
                "query": """
                query {
                    getNewPackagingRequestIds {
                        nextToken
                        packagingRequestIds
                    }
                }
                """
            }

        response = requests.post(api_url, json=req_data, headers=headers)
        data = response.json()

        print(jwt_token)
        print(data)

        assert "data" in data
        assert data["data"] is not None
        assert "getNewPackagingRequestIds" in data["data"]
        return data["data"]["getNewPackagingRequestIds"]

    found = False
    ids = get_ids()
    if order_metadata["orderId"] in ids["packagingRequestIds"]:
        found = True
    while found == False and ids.get("nextToken", None) is not None:
        ids = get_ids(ids["nextToken"])
        if order_metadata["orderId"] in ids["packagingRequestIds"]:
            found = True

    assert found == True

    # Clean database
    table.delete_item(Key={
        "orderId": order_metadata["orderId"],
        "productId": order_metadata["productId"]
    })


def test_get_new_packaging_request_ids_no_iam(api_url):
    """
    Test getNewPackagingRequestIds without IAM permission
    """

    query = """
    query {
        getNewPackagingRequestIds {
            nextToken
            packagingRequestIds
        }
    }
    """

    response = requests.post(api_url, json={"query": query})
    data = response.json()
    assert data.get("data", None) is None
    assert len(data["errors"]) > 0


def test_get_packaging_request(jwt_token, api_url, warehouse_table_name, get_order):
    """
    Test getPackagingRequest
    """

    # Create an order
    order = get_order()
    order_metadata = {
        "orderId": order["orderId"],
        "productId": "__metadata",
        "modifiedDate": order["modifiedDate"],
        "newDate": order["modifiedDate"],
        "status": "NEW"
    }

    # Seed the table
    table = boto3.resource("dynamodb").Table(warehouse_table_name) # pylint: disable=no-member
    with table.batch_writer() as batch:
        batch.put_item(Item=order_metadata)
        for product in order["products"]:
            batch.put_item(Item={
                "orderId": order["orderId"],
                "productId": product["productId"],
                "quantity": product.get("quantity", 1)
            })

    # Perform the query
    query = """
    query ($input: PackagingInput!) {
        getPackagingRequest(input: $input) {
            orderId
            status
            products {
                productId
                quantity
            }
        }
    }
    """

    response = requests.post(api_url,
        headers={"Authorization": jwt_token},
        json={
            "query": query,
            "variables": {"input": {"orderId": order["orderId"]}}
        }
    )
    data = response.json()
    print(data)

    assert "data" in data
    assert data["data"] is not None
    assert "getPackagingRequest" in data["data"]
    pr = data["data"]["getPackagingRequest"]
    assert pr["orderId"] == order["orderId"]
    assert pr["status"] == order["status"]
    assert len(pr["products"]) == len(order["products"])

    products = {p["productId"]: p for p in pr["products"]}
    for product in order["products"]:
        assert product["productId"] in products.keys()
        assert products[product["productId"]]["quantity"] == product.get("quantity", 1)

    # Cleanup
    with table.batch_writer() as batch:
        batch.delete_item(Key={
            "orderId": order["orderId"],
            "productId": "__metadata"
        })
        for product in order["products"]:
            batch.delete_item(Key={
                "orderId": order["orderId"],
                "productId": product["productId"]
            })


def test_start_packaging(jwt_token, api_url, warehouse_table_name):
    """
    Test startPackaging
    """

    order_metadata = {
        "orderId": str(uuid.uuid4()),
        "productId": "__metadata",
        "modifiedDate": datetime.datetime.now().isoformat(),
        "newDate": datetime.datetime.now().isoformat(),
        "status": "NEW"
    }

    print(order_metadata)

    # Seed the database
    table = boto3.resource("dynamodb").Table(warehouse_table_name) # pylint: disable=no-member
    table.put_item(Item=order_metadata)

    # Make request
    query = """
    mutation ($input: PackagingInput!) {
        startPackaging(input: $input) {
            success
        }
    }
    """

    response = requests.post(
        api_url, 
        headers={"Authorization": jwt_token},
        json={
            "query": query,
            "variables": {
                "input": {"orderId": order_metadata["orderId"]}
            }
        })
    data = response.json()
    print(data)
    assert "data" in data
    assert data["data"] is not None
    assert "startPackaging" in data["data"]
    assert "success" in data["data"]["startPackaging"]
    assert data["data"]["startPackaging"]["success"] == True

    ddb_res = table.get_item(Key={
        "orderId": order_metadata["orderId"],
        "productId": order_metadata["productId"]
    })
    assert "Item" in ddb_res
    assert "status" in ddb_res["Item"]
    assert "newDate" not in ddb_res["Item"]
    assert ddb_res["Item"]["status"] == "IN_PROGRESS"

    # Cleanup
    table.delete_item(Key={
        "orderId": order_metadata["orderId"],
        "productId": order_metadata["productId"]
    })


def test_complete_packaging(jwt_token, api_url, warehouse_table_name):
    """
    Test completePackaging
    """

    order_metadata = {
        "orderId": str(uuid.uuid4()),
        "productId": "__metadata",
        "modifiedDate": datetime.datetime.now().isoformat(),
        "status": "IN_PROGRESS"
    }

    print(order_metadata)

    # Seed the database
    table = boto3.resource("dynamodb").Table(warehouse_table_name) # pylint: disable=no-member
    table.put_item(Item=order_metadata)

    # Make request
    query = """
    mutation ($input: PackagingInput!) {
        completePackaging(input: $input) {
            success
        }
    }
    """

    response = requests.post(
        api_url, 
        headers={"Authorization": jwt_token},
        json={
            "query": query,
            "variables": {
                "input": {"orderId": order_metadata["orderId"]}
            }
        })
    data = response.json()
    print(data)
    assert "data" in data
    assert data["data"] is not None
    assert "completePackaging" in data["data"]
    assert "success" in data["data"]["completePackaging"]
    assert data["data"]["completePackaging"]["success"] == True

    ddb_res = table.get_item(Key={
        "orderId": order_metadata["orderId"],
        "productId": order_metadata["productId"]
    })
    assert "Item" in ddb_res
    assert "status" in ddb_res["Item"]
    assert ddb_res["Item"]["status"] == "COMPLETED"

    # Cleanup
    table.delete_item(Key={
        "orderId": order_metadata["orderId"],
        "productId": order_metadata["productId"]
    })


def test_get_new_deliveries(jwt_token, api_url, delivery_table_name, get_order):
    """
    Test getNewDeliveries
    """

    order = get_order()
    delivery = {
        "orderId": order["orderId"],
        "address": order["address"],
        "isNew": "true",
        "status": "NEW"
    }
    print(delivery)

    # Seed the database
    table = boto3.resource("dynamodb").Table(delivery_table_name) # pylint: disable=no-member
    table.put_item(Item=delivery)

    # Make requests
    headers = {"Authorization": jwt_token}
    def get_deliveries(next_token=None):
        if next_token:
            req_data = {
                "query": """
                query ($nextToken: String) {
                    getNewDeliveries(nextToken: $nextToken) {
                        nextToken
                        deliveries {
                            orderId
                            address {
                                name
                                streetAddress
                                city
                                country
                            }
                        }
                    }
                }
                """,
                "variables": {
                    "nextToken": next_token
                }
            }
        else:
            req_data = {
                "query": """
                query {
                    getNewDeliveries {
                        nextToken
                        deliveries {
                            orderId
                            address {
                                name
                                streetAddress
                                city
                                country
                            }
                        }
                    }
                }
                """
            }

        response = requests.post(api_url, json=req_data, headers=headers)
        data = response.json()

        print(data)

        assert "data" in data
        assert data["data"] is not None
        assert "getNewDeliveries" in data["data"]
        return data["data"]["getNewDeliveries"]

    found = False
    deliveries = get_deliveries()
    for delivery in deliveries["deliveries"]:
        if delivery["orderId"] == order["orderId"]:
            found = True
    while found == False and deliveries.get("nextToken", None) is not None:
        deliveries = get_deliveries(deliveries["nextToken"])
        for delivery in deliveries["deliveries"]:
            if delivery["orderId"] == order["orderId"]:
                found = True

    assert found == True

    # Clean database
    table.delete_item(Key={
        "orderId": order["orderId"]
    })


def test_get_delivery(jwt_token, api_url, delivery_table_name, get_order):
    """
    Test getDelivery
    """

    order = get_order()
    delivery = {
        "orderId": order["orderId"],
        "address": order["address"],
        "isNew": "true",
        "status": "NEW"
    }
    print(delivery)

    # Seed the database
    table = boto3.resource("dynamodb").Table(delivery_table_name) # pylint: disable=no-member
    table.put_item(Item=delivery)

    # Make request
    query = """
    query($input: DeliveryInput!) {
        getDelivery(input: $input) {
            orderId
            address {
                name
                streetAddress
                city
                country
            }
        }
    }
    """

    res = requests.post(
        api_url,
        headers={"Authorization": jwt_token},
        json={
            "query": query,
            "variables": {
                "input": {
                    "orderId": order["orderId"]
                }
            }
        }
    )

    data = res.json()
    print(data)
    assert "data" in data
    assert data["data"] is not None
    assert "getDelivery" in data["data"]
    assert "orderId" in data["data"]["getDelivery"]
    assert data["data"]["getDelivery"]["orderId"] == order["orderId"]
    assert data["data"]["getDelivery"]["address"]["name"] == order["address"]["name"]
    assert data["data"]["getDelivery"]["address"]["streetAddress"] == order["address"]["streetAddress"]
    assert data["data"]["getDelivery"]["address"]["city"] == order["address"]["city"]
    assert data["data"]["getDelivery"]["address"]["country"] == order["address"]["country"]

    # Cleanup
    table.delete_item(Key={
        "orderId": order["orderId"]
    })


def test_start_delivery(jwt_token, api_url, delivery_table_name, get_order):
    """
    Test startDelivery
    """

    order = get_order()
    delivery = {
        "orderId": order["orderId"],
        "address": order["address"],
        "isNew": "true",
        "status": "NEW"
    }
    print(delivery)

    # Seed the database
    table = boto3.resource("dynamodb").Table(delivery_table_name) # pylint: disable=no-member
    table.put_item(Item=delivery)

    # Make request
    query = """
    mutation ($input: DeliveryInput!) {
        startDelivery(input: $input) {
            success
        }
    }
    """

    res = requests.post(
        api_url,
        headers={"Authorization": jwt_token},
        json={
            "query": query,
            "variables": {
                "input": {
                    "orderId": order["orderId"]
                }
            }
        }
    )
    data = res.json()
    print(data)

    assert "data" in data
    assert data["data"] is not None
    assert "startDelivery" in data["data"]
    assert "success" in data["data"]["startDelivery"]
    assert data["data"]["startDelivery"]["success"] == True

    ddb_res = table.get_item(Key={
        "orderId": order["orderId"]
    })
    assert "Item" in ddb_res
    assert "status" in ddb_res["Item"]
    assert ddb_res["Item"]["status"] == "IN_PROGRESS"
    assert "isNew" not in ddb_res["Item"]

    # Cleanup
    table.delete_item(Key={
        "orderId": order["orderId"]
    })


def test_fail_delivery(jwt_token, api_url, delivery_table_name, get_order):
    """
    Test failDelivery
    """

    order = get_order()
    delivery = {
        "orderId": order["orderId"],
        "address": order["address"],
        "status": "IN_PROGRESS"
    }
    print(delivery)

    # Seed the database
    table = boto3.resource("dynamodb").Table(delivery_table_name) # pylint: disable=no-member
    table.put_item(Item=delivery)

    # Make request
    query = """
    mutation ($input: DeliveryInput!) {
        failDelivery(input: $input) {
            success
        }
    }
    """

    res = requests.post(
        api_url,
        headers={"Authorization": jwt_token},
        json={
            "query": query,
            "variables": {
                "input": {
                    "orderId": order["orderId"]
                }
            }
        }
    )
    data = res.json()
    print(data)

    assert "data" in data
    assert data["data"] is not None
    assert "failDelivery" in data["data"]
    assert "success" in data["data"]["failDelivery"]
    assert data["data"]["failDelivery"]["success"] == True

    ddb_res = table.get_item(Key={
        "orderId": order["orderId"]
    })
    assert "Item" in ddb_res
    assert "status" in ddb_res["Item"]
    assert ddb_res["Item"]["status"] == "FAILED"

    # Cleanup
    table.delete_item(Key={
        "orderId": order["orderId"]
    })


def test_complete_delivery(jwt_token, api_url, delivery_table_name, get_order):
    """
    Test failDelivery
    """

    order = get_order()
    delivery = {
        "orderId": order["orderId"],
        "address": order["address"],
        "status": "IN_PROGRESS"
    }
    print(delivery)

    # Seed the database
    table = boto3.resource("dynamodb").Table(delivery_table_name) # pylint: disable=no-member
    table.put_item(Item=delivery)

    # Make request
    query = """
    mutation ($input: DeliveryInput!) {
        completeDelivery(input: $input) {
            success
        }
    }
    """

    res = requests.post(
        api_url,
        headers={"Authorization": jwt_token},
        json={
            "query": query,
            "variables": {
                "input": {
                    "orderId": order["orderId"]
                }
            }
        }
    )
    data = res.json()
    print(data)

    assert "data" in data
    assert data["data"] is not None
    assert "completeDelivery" in data["data"]
    assert "success" in data["data"]["completeDelivery"]
    assert data["data"]["completeDelivery"]["success"] == True

    ddb_res = table.get_item(Key={
        "orderId": order["orderId"]
    })
    assert "Item" in ddb_res
    assert "status" in ddb_res["Item"]
    assert ddb_res["Item"]["status"] == "COMPLETED"

    # Cleanup
    table.delete_item(Key={
        "orderId": order["orderId"]
    })