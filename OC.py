import json
import hmac
import hashlib
import base64
import logging
import requests
import boto3
import os
import random
import string
from requests.auth import HTTPBasicAuth
from botocore.exceptions import ClientError
from elavon_processor import *
from paynuity import gwapi
from payroc import process_token_and_send_to_processor
from payroc import process_card_sale
from datetime import datetime
from boto3.dynamodb.conditions import Key, Attr
import xml.etree.ElementTree as ET

# #Elavon flag
# processor_flag = 'paynuity'

# AWS SSM client
ssm = boto3.client('ssm')

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)
 
# Initialize DynamoDB client
dynamodb = boto3.resource('dynamodb')

# DynamoDB table reference for tracking used authorization codes
auth_table = dynamodb.Table('AuthCodesTable')

# save response
response_table = dynamodb.Table("paymentgw_orch_json_response_table")

def fetch_and_process_default_address(send_token):
    # Step 1: Extract the token from orchestrator payload
    token = send_token
    if not token:
        return {
            'status': 'error',
            'message': 'Token missing in orchestrator payload.'
        }
    
    # Step 1: Retrieve the stored data from DynamoDB using the token
    items = []
    response = table.scan(
        FilterExpression=Attr('token').eq(token)
    )
    items.extend(response.get('Items', []))

    while 'LastEvaluatedKey' in response:
        response = table.scan(
            FilterExpression=Attr('token').eq(token),
            ExclusiveStartKey=response['LastEvaluatedKey']
        )
        items.extend(response.get('Items', []))

    if not items:
        return {
            'status': 'error',
            'message': 'Token not found in DynamoDB.'
        }

    # Assuming 'data' is a list and you want the first item
    sortdata = sorted(items, key=lambda x: x['timestamp'])
    stored_data = sortdata[-1]

    # Decode the card_number
    card_number_encoded = stored_data.get("card_number")
    print("card_number_encoded", card_number_encoded)
    if card_number_encoded:
        try:
            # Decode Base64-encoded card number
            decoded_card_number = base64.b64decode(card_number_encoded).decode('utf-8')
        except Exception as e:
            print(f"Error decoding card_number: {e}")
            decoded_card_number = None
    else:
        decoded_card_number = None

    # Requested bins
    bins  = ['222929','222932','222933','222934','223372','223378','223391','223420',
            '223430','223456','223467','223504','223515','223600','223678','232039',
            '234091','235019','236007','238003','246001','256000','271679','557898',
            '436797','436604','498391','446222','450859','458178','482916','471567',
            '486446','486484','486485','517233','517240','553327','485953','511924',
            '524897','534786','539735','553437','556167','558717','555028','428816',
            '486699','412538','512211','521129','522225','552199','558936','543847',
            '460900','460902','478599','517834','525322','528232','538914','542465',
            '543024','552458','555443','555526','555618','555702','555706','555774',
            '555836','555847','558325','543464','460899','460901','460903','463374',
            '428837','540524','555152','404045','405392','414154','432704','454328',
            '426691','439576','439577','439578','493591','433451','440872','441112',
            '451946','489683','519075','536025','556150','559292','559666','404337',
            '405632','429512','493193','518490','439657','439658','439659','439660',
            '439661','517229','518623','521028','521666','522799','524744','531713',
            '531802','532670','532741','537064','510498','542348','552766','553397',
            '553502','553519','554381','556625','434937','439734','516489','527415',
            '531923','533724','557750','558719','531367','517094','491724','515783',
            '416598','491225','493778','535456','539571','555336','463389','493724',
            '525177','525797','525828','525847','527901','532091','534092','412925',
            '428910','429385','436767','438585','447299','448137','456599','461664',
            '464095','474225','535067','450434','452374','485636','536489','538395',
            '539502','539578','486695','539186','428836','447420','476715','483317',
            '489607','491090','559147','519315','522981','531797','539593','557271',
            '512219','471262','471299','471440','472205','533038','556963','493875',
            '485973','520275','529364','532329','534633','552784','555115','555119',
            '555244']

    default_address = {}
    if decoded_card_number:
        if decoded_card_number[:6] in bins:
            default_address['address1'] = '1111 Expedia Group Wy W'
            default_address['address2'] = ''
            default_address['city'] = 'Seattle'
            default_address['state'] = 'WA'
            default_address['zip_code'] = '98119'
            default_address['country'] = 'United States'
            default_address['status'] = True
            default_address['first_name'] = stored_data.get("cardholder_name").split(' ')[0]
            default_address['last_name'] = stored_data.get("cardholder_name").split(' ')[1]
            default_address['company'] = 'na'
            default_address['email'] = 'noreply@expedia.com'
        else:
            default_address['status'] = False
    else:
        default_address['status'] = False

    return default_address

def generate_random_id(length=10):
    """Generate a random alphanumeric ID."""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def save_payload_to_dynamodb(payload, processor_flag, transaction_id):
    """
    Save the payload to the DynamoDB table as a string, with silent error handling.
    
    Args:
        payload (dict or str): The payload to save. Can be a dict or a JSON-formatted string.
        processor_flag (str): A flag to indicate the processor type or usage context.
    """
    try:
        # Convert payload to JSON string if it's not already
        if not isinstance(payload, str):
            payload_str = json.dumps(payload)
        else:
            # Reformat string to ensure it's valid JSON
            payload_str = json.dumps(json.loads(payload))
        
        # Generate a random alphanumeric ID for the primary key
        random_id = generate_random_id()
        
        # Prepare the item to store in DynamoDB
        item = {
            "id": random_id,  # Primary key
            "transaction_id": transaction_id,
            "payload": payload_str,
            "processor_flag": processor_flag  # Additional column for processor context
        }
        
        # Save the item to DynamoDB
        response_table.put_item(Item=item)
        print(f"Payload successfully saved with ID: {random_id} and processor_flag: {processor_flag}")
    except json.JSONDecodeError as e:
        print(f"[Silent Error] Invalid JSON format in payload. Details: {e}")
    except Exception as e:
        print(f"[Silent Error] Failed to save payload to DynamoDB. Details: {e}")
 
def determine_terminal_app_id(agency, currency_code):
    
    if currency_code == 'CAD':
        country_code = 'CAN'
    else:
        country_code = 'USA'
    
    # Define constants
    TERMINALS = {
        "USA": {
            "ecommerce": {"terminal_id": "0017340008025557375191", "application_id": "TZ9999IC"},
            "moto": {"terminal_id": "00173400080257391040", "application_id": "TZ9999MC"},
            "currency": "USD"
        },
        "CAN": {
            "ecommerce": {"terminal_id": "0089250008025557433284", "application_id": "TZ9999IC"},
            "moto": {"terminal_id": "0089250008025557441121", "application_id": "TZ9999MC"},
            "currency": "CAD"
        }
    }
    AGENCIES = {"Flair Air", "Air Black Box", "Air Black Box USD",
    "Flairair", "FlairAir Public IBE", "Flair Air Web Agency"}

    # Determine type based on agency
    transaction_type = "ecommerce" if agency in AGENCIES else "moto"

    # Test cases
    if agency == "":
        transaction_type = "ecommerce"
        country_code = "CAN"

    # Select terminal, application ID, and currency
    terminal_data = TERMINALS.get(country_code)
    if not terminal_data:
        raise ValueError(f"Country {country_code} not supported")
    
    terminal_id = terminal_data[transaction_type]["terminal_id"]
    application_id = terminal_data[transaction_type]["application_id"]
    currency = terminal_data["currency"]
    
    return terminal_id, application_id, currency, transaction_type

def generate_unique_auth_code():
    # Define allowed characters: uppercase letters and digits
    allowed_chars = string.ascii_uppercase + string.digits
    while True:
        # Generate a random 6-character code
        auth_code = ''.join(random.choices(allowed_chars, k=6))
        
        # Check DynamoDB to ensure uniqueness
        try:
            response = auth_table.get_item(Key={'authorization_code': auth_code})
            if 'Item' not in response:
                # If the code is not found in DynamoDB, store it and return
                auth_table.put_item(Item={'authorization_code': auth_code})
                return auth_code
        except ClientError as e:
            print(f"Error accessing DynamoDB: {e}")
            return None
 
def store_transaction(pnr, agency, country, terminal_id, application_id, currency, transaction_type):
    try:
        # Use the 'Table' object to put items into DynamoDB
        table = dynamodb.Table('PaymentGateway_Orchestrator_Terminal_Data')
        table.put_item(
            Item={
                'PNR': pnr,
                'AgencyName': agency,
                'Country': country,
                'TerminalID': terminal_id,
                'ApplicationID': application_id,
                'Currency': currency,
                'TransactionType': transaction_type
            }
        )
    except ClientError as e:
        print(f"Error storing transaction data: {e}")
        return False
    return True

# Set up a counter table in DynamoDB to track attempts per transaction
# dynamodb = boto3.resource('dynamodb')
# table = dynamodb.Table('3DSAttemptsTable')

def get_parameter(param_name):
    ssm = boto3.client('ssm')
    response = ssm.get_parameter(Name=param_name, WithDecryption=True)
    return response['Parameter']['Value']
    
def get_auth_token_from_parameter_store():
    """Retrieve JWT token from AWS Parameter Store"""
    try:
        response = ssm.get_parameter(
            Name='/jwt/token/dev/Vikram',
            WithDecryption=True  # Decrypt if stored encrypted
        )
        return response['Parameter']['Value']
    except ClientError as e:
        logger.error(f"Error retrieving token from SSM: {e}")
        return None

# def send_to_3ds_api(auth_token):
#     api_key = '/3ds/api-key'
#     response = ssm.get_parameter(Name=api_key, WithDecryption=True)
#     threeds_api_key = response['Parameter']['Value']
#     # Retrieve API key from Parameter Store
    
#     token = '/3ds/Bearer-token'
#     response = ssm.get_parameter(Name=token, WithDecryption=True)
#     Bearer_token = response['Parameter']['Value']
    
#     print(threeds_api_key, Bearer_token)
#     """Send request to 3DS API"""
#     three_ds_api_url = "https://api-sandbox.3dsintegrator.com/v2.2/authenticate/browser"
#     three_ds_api_headers = {
#         "Accept": "application/json",
#         "Content-Type": "application/json",
#         "X-3DS-API-KEY": threeds_api_key,
#         "Authorization": f"Bearer {Bearer_token}"
#     }
#     three_ds_api_data = {
#         "browser": {
#             "browserAcceptHeader": "application/json",
#             "browserLanguage": "en-US",
#             "browserUserAgent": "Mozilla/5.0..."
#         },
#         "challengeIndicator": "01",
#         "amount": 150.25,
#         "month": "08",
#         "year": "22",
#         "pan": "4005519200000004",
#         "threeDSRequestorURL": "https://flyflair.com"
#     }
#     three_ds_api_response = requests.post(
#         three_ds_api_url,
#         headers=three_ds_api_headers,
#         data=json.dumps(three_ds_api_data)
#     )
#     three_ds_api_response.raise_for_status()
#     three_ds_api_response_json = three_ds_api_response.json()
#     logger.info(f"3DS API Response: {three_ds_api_response.json()}")
#     #return three_ds_api_response.json()
#     return three_ds_api_response_json
#     #{
    #'statusCode': 200,
    #'body': json.dumps({'message': 'Sent to 3DS API', 'threeDsResponse': str(three_ds_api_response_json)})
    #}

def route_to_processor(processor):

    random_value = random.random()
    print("random_value-------->", random_value)
    if processor in ["Paynuity", "Elavon"]:
        if random_value < 0.000:
            return processor
        else:
            return "Payroc"
    return processor


def lambda_handler(event, context):
    try:
        # Define agency-to-processor mappings
        #payroc_agencies = ["ETRAVELI-F8"]
        elavon_agencies = ["agency7", "agency8", "agency9"]
        paynuity_agencies = ["ETRAVELI-F8"]

        # Retrieve API Base URL from Parameter Store
        param_name_api_base_url = '/intelysisapi'
        response = ssm.get_parameter(Name=param_name_api_base_url, WithDecryption=True)
        base_url = response['Parameter']['Value']

        # Retrieve x-user-id and API key from Parameter Store
        param_name_x_user_id = '/myapi/user_id'
        param_name_api_key = '/myapi/api_key'
        uid = ssm.get_parameter(Name=param_name_x_user_id, WithDecryption=True)['Parameter']['Value']
        key_param = ssm.get_parameter(Name=param_name_api_key, WithDecryption=True)['Parameter']['Value']
        key = key_param.encode('utf-8')
        uid_bytes = uid.encode('utf-8')

        # Extract the token reference
        token = event['token']
        pnr = token.split('-')[1] if '-' in token else token
        print("PNR------>", pnr)

        # Retrieve username and password from Parameter Store
        parameter_username = '/flyflair/dev/username'
        parameter_password = '/flyflair/dev/password'
        username = get_parameter(parameter_username)
        password = get_parameter(parameter_password)

        # Fetch passenger data
        url = f'{base_url}?reservationLocator={pnr}'
        response = requests.get(url, auth=HTTPBasicAuth(username, password))
        response.raise_for_status()  # Raise exception for 4xx or 5xx status codes
        passenger_data = response.json()
        #print("passenger_data------>", passenger_data)

        if passenger_data:
            agency_name = passenger_data[0].get("bookingInformation", {}).get("agency", {}).get("name", "")
            currency_code = passenger_data[0].get("bookingInformation", {}).get("currency", {}).get("code", "")
            reservation_key = passenger_data[0].get("key", "")
            
            # Extracting fields
            reservation_summary = passenger_data[0].get("reservationSummary", {})
            passenger_profile = reservation_summary.get("passenger", {}).get("reservationProfile", {})
            booking_info = passenger_data[0].get("bookingInformation", {})
            contact_info = booking_info.get("contactInformation", {})

            # Fields
            first_name = passenger_profile.get("firstName", "na")
            last_name = passenger_profile.get("lastName", "na")
            company = booking_info.get("company", "na")
            email = contact_info.get("email", "na")
            
            print("agency_name------>", agency_name)
            print("currency_code------>", currency_code)
            print("reservation_key------>", reservation_key)

            # reservationapi = f'https://flairair-api.intelisys.ca/RESTv1/reservations/{reservation_key}'
            # response = requests.get(reservationapi, auth=HTTPBasicAuth(username, password))
        else:
            first_name = 'na'
            last_name = 'na'
            company = 'na'
            email = 'na'
            agency_name = ""
            currency_code = ""
            reservation_key = ""
            
        # Fetch billing data
        try:
            if reservation_key !="":
                reservationapi = f'{base_url}/{reservation_key}'
                response = requests.get(reservationapi, auth=HTTPBasicAuth(username, password))
                response.raise_for_status()  # Raise exception for billing data
                billing_data_full = response.json()
            else:
                billing_data_full = None
        except:
            print("You see i cant help it Error: No passenger data")
            
        #print("billing_data_full------>", billing_data_full)

        # Determine processor based on agency
        if agency_name in paynuity_agencies:
            processor = "Paynuity"
        elif agency_name in elavon_agencies:
            processor = "Elavon"
        else:
            processor = "Payroc"
        
        processor = "Payroc"
        print(f"Initial processor selected: {processor}")

        # Apply probabilistic filter if needed
        processor = route_to_processor(processor)
        print(f"Final processor selected: {processor}")
        processor_flag = processor
        print(f"Final processor selected: {processor}")

        if billing_data_full:
            billing_data_full = response.json()
            #print("billing_data_full------>", billing_data_full)

            paymentTransactions = billing_data_full.get('passengers', {})
            #print("paymentTransactions------>", paymentTransactions)

            if paymentTransactions:
                address = paymentTransactions[0].get('reservationProfile',{}).get('address', {})
                address1 = address.get('address1', 'na')
                address2 = address.get('address2', 'na')
                city = address.get('city', 'na')

                # Handle country, which might be None or not have a name key
                locator_data = address.get('location', {})
                country_data = locator_data.get('country', {}) if locator_data else None
                country = country_data.get('name', 'na') if country_data else 'na'

                # Handle province, which might be None or not have a name key
                province_data = locator_data.get('province', {}) if locator_data else None
                province = province_data.get('name', 'Alberta') if province_data else 'Alberta'

                # Handle province, which might be None or not have a name key
                state_data = locator_data.get('state', {}) if locator_data else None
                state = state_data.get('name', 'Alberta') if state_data else 'Alberta'

                zip_code = address.get('postalCode', 'na')
                phone_number = paymentTransactions[0].get('reservationProfile',{}).get(
                    'personalContactInformation', {}).get('phoneNumber', 'na')

                if province_data:
                    state = province

                
            else:
                print("ADDRESS NULL ------>")
                # Output the extracted details
                address1 = 'na'
                address2 = 'na'
                city = 'na'
                country = 'na'
                province = 'na'
                zip_code = 'na'
                phone_number= 'na'
        else:
            # Output the extracted details
            address1 = 'na'
            address2 = 'na'
            city = 'na'
            country = 'na'
            province = 'na'
            zip_code = 'na'
            phone_number= 'na'

        # Output the extracted details
        print("Address Line 1:---------->", address1)
        print("Address Line 2:---------->", address2)
        print("City:---------->", city)
        print("Country:---------->", country)
        print("Province:---------->", province)
        print("Postal Code:---------->", zip_code)
        print("Phone Number:---------->", phone_number)
        
        if billing_data_full:
            journeys = billing_data_full.get('journeys', [{}])
            #print("journeys:---------->", journeys)
        else:
            journeys = None

        if journeys:
            departure_info = journeys[0].get('departure', {}) if journeys else {}
            scheduled_time_str = departure_info.get('scheduledTime', '')
        else:
            scheduled_time_str = None
            departure_info = None

        print("scheduled_time_str:---------->", scheduled_time_str)
        print("departure_info:---------->", departure_info)

        # if scheduled_time_str:
        #     try:
        #         departuredate = datetime.strptime(scheduled_time_str, '%Y-%m-%d %H:%M:%S%z')
        #     except ValueError:
        #         departuredate = None  # Handle parsing error
        # else:
        #     departuredate = None

        # # Safely get departure and arrival city
        # if departure_info:
        #     departurecity = departure_info.get('airport', {}).get('code', '') 
        # else:
        #     departurecity = None
        # arrival_info =  journeys[0].get('segments', {})[0].get('arrival', {}) if journeys else {}
        # if arrival_info:
        #     destinationcity = arrival_info.get('airport', {}).get('code', '')
        # else:
        #     destinationcity = None
        if scheduled_time_str:
            try:
                departuredate = datetime.strptime(scheduled_time_str, '%Y-%m-%d %H:%M:%S%z')
            except ValueError:
                departuredate = None  # Handle parsing error
        else:
            departuredate = None

        # Safely get departure and arrival city
        departurecity = None
        arrivalcity = None

        if departure_info:
            if departure_info.get('airport'):
                departurecity = departure_info['airport'].get('code', '')
            else:
                departurecity = None
        else:
            departurecity = None

        arrival_info = journeys[0].get('segments', [{}])[0].get('arrival', {}) if journeys else {}
        if arrival_info:
            arrivalcity = arrival_info.get('airport', {}).get('code', '')
        else:
            arrivalcity = None

        # Use the safely obtained city codes
        departurecity = departurecity if departurecity else None
        arrivalcity = arrivalcity if arrivalcity else None

        # Use the correct variable name
        destinationcity = arrivalcity



        print("departurecity:---------->", departurecity)
        print("destinationcity:---------->", destinationcity)

        # Set static values
        airlinecode = 'F8'
        serviceclass = 'economy'
        stopovercode = ''

        # Set static values
        restrictedticket = ''
        exchangeticket = ''
        internetindicator = 'online'
        electronicindicator = 'electronic'

        # Safely get fare class
        if billing_data_full:
            passenger_details = billing_data_full.get('journeys', [{}])[0]
        else:
            passenger_details = None

        if passenger_details:
            fareClass = passenger_details.get('passengerJourneyDetails', {})[0].get('fareClass', {}).get('code')
        else:
            fareClass = ''

        print('fareClass--------------->', fareClass)

        # Safely parse ticket issue date
        if billing_data_full:
            payment_transactions = billing_data_full.get('paymentTransactions', [{}])
        else:
            payment_transactions = None

        if payment_transactions:
            payment_time_str = payment_transactions[0].get('paymentTime', '')
        else:
            payment_time_str = None

        if payment_time_str:
            try:
                ticketissuedate = datetime.strptime(payment_time_str, '%Y-%m-%d %H:%M:%S%z')
            except ValueError:
                ticketissuedate = datetime.now()  # Fallback to today's date
        else:
            ticketissuedate = datetime.now()

        # Print agency for debugging
        print("Agency------->", agency_name)

        # Safely determine legs value
        if passenger_details:
            fare_class_description = passenger_details.get('passengerJourneyDetails', {})[0].get('fareClass', {}).get('description', '')
            legs_value = 1 if fare_class_description == 'One Way Full-Fare' else 2
        else:
            fare_class_description = ''
            legs_value = 2

        # Set static value
        conjunction = ''
        
        # Generate a unique authorization code for this transaction
        auth_code = generate_unique_auth_code()
        print("auth_code------>", auth_code)

        if auth_code is None:
            return {'statusCode': 500, 'body': 'Error generating authorization code'}
        
        # Retrieve the authorization token from Parameter Store
        auth_token = get_auth_token_from_parameter_store()
        print("auth_token------>", auth_token)

        if not auth_token:
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Unable to retrieve authorization token'})
            }

        # Parse JSON from the event
        body_json = json.loads(json.dumps(event))
        #print("Body --->", body_json)
        card_details = body_json
        card_number = body_json.get('card_data', "")
        expiry_month = str(body_json.get('expiry_month', ""))
        expiry_year = str(body_json.get('expiry_year', ""))
        expiry_date_elavon = f"{expiry_month}{expiry_year[-2:]}"
        expiry_date_paynuity = f"{expiry_month}{expiry_year[-2:]}" if len(expiry_month)>1 else f"0{expiry_month}{expiry_year[-2:]}"
        card_data = f"{str(card_number)}={str(expiry_date_elavon)}"
        agency = agency_name 
        currency = currency_code 
        country = country 

        # Extract the last four digits of the card number
        last_four_digits = str(card_number)[-4:]
        
        name_on_card = card_details.get("name_on_card", "")
        first_name = first_name
        last_name = last_name
        email_address = email
        transaction_id = body_json.get("transaction_id", "")
        transaction_amount = body_json.get('payment', {}).get("amount", 0)
        street_address = card_details.get("address", "")
        cvv = body_json.get('csc', "") 

        # Uncomment Address --------------------> 
        # Address copy
        # # Paynuity needs
        address_data = body_json.get('billing_address')
        if address_data:
            print("New Address Found---------->")
            address1 = address_data.get('street1', '')
            address2 = address_data.get('street2', '')
            city = address_data.get('city', '')
            state = address_data.get('state', '')
            country = address_data.get('country', '')
            zip_code = address_data.get('postal', '')

        # Address check again
        print("Updated Address-------->")
        print("Address Line 1 Update:---------->", address1)
        print("Address Line 2 Update:---------->", address2)
        print("City Update:---------->", city)
        print("Country Update:---------->", country)
        print("Province Update:---------->", province)
        print("Postal Code Update:---------->", zip_code)
        
        
        if not pnr or not country or not agency:
            return {"statusCode": 400, "body": "PNR, country, and agency are required"}

        # Determine terminal, application ID, and currency
        try:
            terminal_id, application_id, currency_code, transaction_type = determine_terminal_app_id(agency, currency_code)
            print("terminal_id, application_id, currency_code, transaction_type------>", terminal_id, application_id, currency_code, transaction_type)
        except ValueError as e:
            return {"statusCode": 400, "body": str(e)}
        
        # Store transaction details in DynamoDB
        success = store_transaction(pnr, agency, country, terminal_id, application_id, currency, transaction_type)
        if not success:
            return {"statusCode": 500, "body": "Error storing transaction data"}

        # Construct the elavon response
        formatted_responses = {
            "transaction_id": transaction_id,
            "terminal_id": terminal_id,
            "application_id": application_id,
            "currency": currency_code,
            "agency": agency_name,
            "transaction_type": transaction_type,
            "name_on_card": name_on_card,
            "last_four_digits": last_four_digits,
            "expiry_month": expiry_month,
            "expiry_year": expiry_year,
            "transaction_amount": transaction_amount,
            "zip_code": zip_code,
            "street_address": street_address,
            "token": token
        } 

        bool_value = True
        print(formatted_responses)

        # Print values for debugging
        logger.info(f"Name on card: {name_on_card}, Expiry Month: {expiry_month}, Expiry Year: {expiry_year}")
        logger.info(f"Boolean Value: {bool_value}")

        if bool_value:
            if processor_flag == 'Elavon':
                print("-------- IN ELAVON Processor----------")
                try:
                    # Call Elavon API and process response
                    elavon_response = send_to_elavon_api(
                        terminal_id, application_id, currency_code,
                        transaction_type, auth_token, transaction_id, card_data, 
                        transaction_amount, zip_code, street_address
                    )

                    print(
                        'Elavon Response ------>', json.dumps({'elavon_response': elavon_response})
                    )

                    #saving payload
                    save_payload_to_dynamodb(elavon_response, processor_flag, transaction_id)

                    # Parse the JSON body
                    response_body = json.loads(elavon_response['body'])
                    elavon_response_data = response_body.get('elavon_response', {})
                    response_xml = elavon_response_data.get('response', '')

                    # Parse the XML
                    try:
                        root = ET.fromstring(response_xml)
                        # Attempt to find the Authorization_Response
                        auth_response = root.find(".//Authorization_Response")
                        if auth_response is not None and auth_response.text:
                            status_value = auth_response.text
                        else:
                            # Fallback to the raw response string
                            status_value = response_xml
                    except ET.ParseError:
                        # Handle invalid XML and fallback to the raw response string
                        status_value = response_xml

                    print("Status Value:", status_value)

                    # Construct formatted_response based on status_value
                    if "APPROVAL" in status_value:
                        formatted_response = {
                            "message": "",
                            "details": {
                                "authorization_code": auth_code,
                                "terminal_id": terminal_id,
                                "application_id": application_id,
                                "currency": currency_code,
                                "agency": agency_name,
                                "transaction_type": transaction_type,
                                "card_last_four_digits": last_four_digits,
                                "card_type": "MCRD",
                                "expiry_date": expiry_month + expiry_year,
                                "transaction_id": transaction_id,
                                "token": token,
                            },
                        }
                    else:
                        formatted_response = {
                            "message": f"Error - Authorization Response: {status_value}",
                            "details": {
                                "response_xml": response_xml,
                            },
                        }

                    # Output the formatted_response
                    print(formatted_response)
                    return formatted_response  
                except Exception as e:
                    logger.error(f"Unexpected error: {e}")
                    return {
                        'message': f"Request Exception Hit Elavon - Response:{elavon_response.json()}",
                        'details': {'statusCode': 500,
                        'ElavonResponse': json.dumps({'ElavonResponse': str(elavon_response.json()) if 'elavon_response' in locals() else None})}
                    }


            elif processor_flag == 'Paynuity':
                try:
                    # Fetching token information
                    default_address = fetch_and_process_default_address(event['token'])

                    #Env mapping
                    AGENCIES = {"Air Black Box", "Air Black Box USD",
                                "Flairair", "FlairAir Public IBE", "Flair Air Web Agency"}
                    #currency_code = "CAD"
                    if (agency_name and currency_code):
                        if agency_name in AGENCIES:
                            print("***********DIRECT***************")
                            if currency_code == 'CAD':
                                print("***********CAD***************")
                                login_id = '5gS5gKm7Hc7352g4wN8Nd2u4SrNw6n5v'
                            else:
                                print("***********USD***************")
                                login_id = 'T8fqsP8ZY4R6fw473rYX72B36f82S4Z4'
                        else:
                            print("***********INDIRECT***************")
                            if currency_code == 'CAD':
                                print("***********CAD***************")
                                login_id = 'V96U3X7NHfbYax5334q2T7CF3evdKKM3'
                            else:
                                print("***********USD***************")
                                login_id = 'vnHtH48275ZFY4YTC2BM2b4sWE2g6BNd'
                    else:
                        return {
                            'status': 500,
                            'message': 'No Agency and Currency Name Found.'
                        }

                    # Process Paynuity transaction
                    transaction_amount_paynuity = int(transaction_amount) / 100
                    print('transaction_amount:-------->', int(transaction_amount))
                    print('transaction_amount_paynuity:-------->', transaction_amount_paynuity)
                    
                    # call paynuity and login
                    paynuity = gwapi()
                    paynuity.setLogin(login_id)  # Replace with actual credentials

                    # Set billing and shipping details
                    if default_address['status'] == True:
                        paynuity.setBilling(
                            default_address['first_name'], default_address['last_name'],
                            default_address['company'], default_address['address1'],
                            default_address['address2'], default_address['city'],
                            default_address['state'], default_address['zip_code'],
                            default_address['country'], default_address['phone_number'],
                            fax='na', email=default_address['email'], website='na'
                        )
                    else:
                        paynuity.setBilling(
                            first_name, last_name, company, address1, address2, city,
                            state, zip_code, country, phone_number, fax='na', email=email_address, website='na'
                        )

                    # set level 
                    paynuity.setLevel3(pnr,name_on_card, departuredate, departurecity, airlinecode,
                        serviceclass, stopovercode, destinationcity, agency, restrictedticket,
                        transaction_amount_paynuity,
                        currency_code, exchangeticket, internetindicator, electronicindicator,
                        conjunction,
                        ticketissuedate, legs_value, fareClass)

                    # set shipping
                    #paynuity.setShipping(
                    #    first_name, last_name, company, address1, address2, city,
                    #    zip_code, country, email_address
                    #)

                    # set order
                    #paynuity.setOrder("1234", "Big Order", 1, 2, "PO1234", "65.192.14.10")

                    # Perform the sale
                    paynuity_response_code, paynuity_response = paynuity.doSale(
                        transaction_amount_paynuity, card_number, expiry_date_paynuity, cvv
                    )
                    print(paynuity_response_code)

                    if int(paynuity_response_code) == 1:
                        paynuity_final_response = {
                            'statusCode': 200,
                            'message': "Approved",
                            'body': json.dumps({'paynuity_response': paynuity_response})
                        }
                    elif int(paynuity_response_code) == 2:
                        paynuity_final_response = {
                            'statusCode': 403,
                            'message': "Declined",
                            'body': json.dumps({'paynuity_response': paynuity_response})
                        }
                    elif int(paynuity_response_code) == 3:
                        paynuity_final_response = {
                            'statusCode': 500,
                            'message': "Error",
                            'body': json.dumps({'paynuity_response': paynuity_response})
                        }
                    print(paynuity_final_response)

                    #saving payload
                    save_payload_to_dynamodb(paynuity_response, processor_flag, transaction_id)
                    
                    #Auth code
                    body2 = json.loads(paynuity_final_response['body'])

                    if body2.get('paynuity_response', {}).get('authcode'):
                        print("check auth--->",body2['paynuity_response']['authcode'])
                        authpaynutiy = body2['paynuity_response']['authcode']
                    else:
                        auth_code = 'NA'

                    # Extract the 'authcode' from the nested structure
                    #auth_code = body2['paynuity_response']['authcode']
                                            
                    if int(paynuity_response_code) == 3:
                        formatted_response = {
                            "message": f"Error - Reason {paynuity_response.get('responsetext', 'N/A')}",
                            "details": {
                                #"authorization_code": auth_code,
                                "terminal_id": terminal_id,
                                "application_id": application_id,
                                "currency": currency_code,
                                "agency": agency_name,
                                "transaction_type": transaction_type,
                                "card_last_four_digits": last_four_digits,
                                "card_type": "MCRD",
                                "expiry_date": expiry_month + expiry_year,
                                "transaction_id": transaction_id,
                                "token": token,
                                "processor_response": paynuity_final_response
                            }
                        }
                    elif int(paynuity_response_code) == 2:
                        formatted_response = {
                            "message": f"Declined - Reason {paynuity_response.get('responsetext', 'N/A')}",
                            "details": {
                                #"authorization_code": auth_code,
                                "terminal_id": terminal_id,
                                "application_id": application_id,
                                "currency": currency_code,
                                "agency": agency_name,
                                "transaction_type": transaction_type,
                                "card_last_four_digits": last_four_digits,
                                "card_type": "MCRD",
                                "expiry_date": expiry_month + expiry_year,
                                "transaction_id": transaction_id,
                                "token": token,
                                "processor_response": paynuity_final_response
                            }
                        }
                    elif int(paynuity_response_code) == 1:
                        formatted_response = {
                            "message": "",
                            "details": {
                                "authorization_code": authpaynutiy,
                                "terminal_id": terminal_id,
                                "application_id": application_id,
                                "currency": currency_code,
                                "agency": agency_name,
                                "transaction_type": transaction_type,
                                "card_last_four_digits": last_four_digits,
                                "card_type": "MCRD",
                                "expiry_date": expiry_month + expiry_year,
                                "transaction_id": transaction_id,
                                "token": token,
                                "processor_response": paynuity_final_response
                            }
                        }
                    else:
                        formatted_response = {
                            "message": "Unknown Paynuity Response Code",
                            "details": {}
                        }

                    return formatted_response

                except requests.exceptions.RequestException as e:
                    logger.error(f"HTTP request error: {e}")
                    return {
                        "message": f"Request Exception Hit Paynuity - Response:{paynuity_final_response.json()}",
                        "details": {'statusCode': 500,
                        'Paynuity Response': json.dumps({'PaynuityResponse': str(paynuity_final_response.json())})}
                    }

            else:
                print("-------in Else condition x Payroc-----")
                try:
                    # Parse JSON from the event
                    body_json = json.loads(json.dumps(event))
                    #print('event ------>', body_json)
                    terminal_id = event['terminal_id']
                    transaction_type = body_json.get('transaction_type', '')
                    transaction_amount = body_json.get('payment', {}).get('amount', '0')
                    transaction_amount = int(transaction_amount)
                    token = body_json.get('token', '')
                    parts = token.split("-")
                    pnr = parts[1] if len(parts) > 1 else parts[0]
                    card_number = body_json.get('card_data', '')
                    expiry_month = int(body_json.get('expiry_month', '01'))
                    expiry_year = int(body_json.get('expiry_year', '2029'))
                    cvv = body_json.get('csc', '')

                    #print("Card Details")
                    #print(card_number,expiry_month,expiry_year,cvv)

                    # Agency from intelisys
                    ## Retrieve x-user-id from Parameter Store
                    param_name_x_user_id = '/myapi/user_id'
                    response = ssm.get_parameter(Name=param_name_x_user_id, WithDecryption=True)
                    uid = response['Parameter']['Value']
                    
                    ## Retrieve API key from Parameter Store
                    param_name_api_key = '/myapi/api_key'
                    response = ssm.get_parameter(Name=param_name_api_key, WithDecryption=True)
                    key_param = response['Parameter']['Value']
                    key = key_param.encode('utf-8')
                    
                    ## Convert uid to bytes
                    uid_bytes = uid.encode('utf-8')
                    
                    ## Extract the token reference
                    token = event['token']
                    
                    ## Print the token reference
                    pnr = token.split('-')
                    if len(pnr)>1:
                        pnr = pnr[1]
                    print("PNR------>", pnr)
                    
                    base_url = 'https://flairair-api.intelisystraining.ca/RESTv1/reservations'
                    url = '{}?reservationLocator={}'.format(base_url, pnr)
                    
                    parameter_username = '/flyflair/dev/username'
                    parameter_password = '/flyflair/dev/password'
                    
                    ## Retrieve username and password from Parameter Store
                    username = get_parameter(parameter_username)
                    password = get_parameter(parameter_password)
                    
                    response = requests.get(url, auth=HTTPBasicAuth(username, password))
                    response.raise_for_status()  # Raise an exception for 4xx or 5xx status codes
                    passenger_data = response.json()
                    #print("passenger_data------>", passenger_data)
                    
                    if passenger_data:
                        agency_name = passenger_data[0].get("bookingInformation", {}).get("agency", {}).get("name", "")
                        print("agency_name------>", agency_name)
                    else:
                        agency_name = None
                        print("agency_name------>", agency_name)

                    payroc_user_id = event.get('payroc-user-id')
                    payroc_x_message_hash = event.get('payroc-x-message-hash')

                    # Reconstruct the payload for Payroc

                    # Get the current date and time
                    current_datetime = datetime.now()

                    # Format as YYMMDDHHMMSS
                    formatted_number = int(current_datetime.strftime("%y%m%d%H%M%S"))
                    
                    orchestrator_payload = {
                        'terminal_id': terminal_id,
                        'transaction_type': transaction_type,
                        'reference': f"{terminal_id}-{pnr}-{formatted_number}",
                        'payment': {'amount': transaction_amount},
                        'token_for_search': token,
                        'token': f'FLAIRPROD-{pnr}-{random.randint(4000000000, 5999999999)}',
                        'card_data': card_number,
                        'expiry_month': expiry_month,
                        'expiry_year': expiry_year,
                        'csc': cvv,
                        'payroc-user-id': payroc_user_id,
                        'payroc-x-message-hash': payroc_x_message_hash,
                        'agency_name': agency_name
                    }
                    #print("Orchestrator Payload ------>", orchestrator_payload)

                    # Call the Payroc function
                    payroc_add_token_response, uidb64, dig_value = process_token_and_send_to_processor(orchestrator_payload)
                    print("payroc-respons------>", payroc_add_token_response)
                    print("payroc_add_token_response ----->", payroc_add_token_response.get('status'))
                    
                    # Handle the Payroc response
                    if payroc_add_token_response.get('status') == 'success':
                        payroc_sale_response = process_card_sale(orchestrator_payload, uidb64, dig_value)

                        print("payroc_sale_response ----->", payroc_sale_response)

                        #saving payload
                        save_payload_to_dynamodb(payroc_sale_response, processor_flag, transaction_id)
                        
                        if payroc_sale_response.get('status') == 'success':
                            
                            # Sale Success
                            print("payroc_sale_response_type -----> SUCCESS")
                            formatted_response = {
                                "message": "",
                                "details": {
                                    "authorization_code": payroc_sale_response.get('details', {}).get('authorization_code', ''),
                                    "terminal_id": terminal_id,
                                    # "application_id": application_id,
                                    # "currency": currency_code,
                                    # "agency": agency_name,
                                    # "transaction_type": transaction_type,
                                    # "card_last_four_digits": last_four_digits,
                                    "card_type": "MCRD",
                                    "expiry_date": expiry_month + expiry_year,
                                    "token": token,
                                    "processor_response": payroc_sale_response
                                }
                            }
                            print(formatted_response)
                            return formatted_response

                        else:

                            # Sale Error
                            formatted_response = {
                            "message" : f"Declined Card Sale - Reason: {json.loads(payroc_sale_response.get('message', '{}')).get('message', 'N/A') if isinstance(payroc_sale_response.get('message'), str) else 'N/A'}",
                            "details": {
                                "authorization_code": "",
                                "terminal_id": terminal_id,
                                # "application_id": application_id,
                                # "currency": currency_code,
                                # "agency": agency_name,
                                "transaction_type": transaction_type,
                                # "card_last_four_digits": last_four_digits,
                                "card_type": "MCRD",
                                "expiry_date": expiry_month + expiry_year,
                                "token": token,
                                "processor_sale_token_response": payroc_sale_response
                                }
                            }
                            print(formatted_response)

                            #saving payload
                            save_payload_to_dynamodb(payroc_sale_response, processor_flag, transaction_id)

                            return formatted_response
                    else:

                        # Add token Error
                        formatted_response = {
                            "message": f"Declined Add Token - Reason: {json.loads(payroc_add_token_response.get('message', '{}')).get('message', 'N/A') if isinstance(payroc_add_token_response.get('message'), str) else 'N/A'}",
                            "details": {
                                "authorization_code": "",
                                "terminal_id": terminal_id,
                                # "application_id": application_id,
                                # "currency": currency_code,
                                # "agency": agency_name,
                                "transaction_type": transaction_type,
                                # "card_last_four_digits": last_four_digits,
                                "card_type": "MCRD",
                                "expiry_date": expiry_month + expiry_year,
                                "token": token,
                                "processor_add_token_response": payroc_add_token_response
                            }
                        }
                        print(formatted_response)

                        #saving payload
                        save_payload_to_dynamodb(payroc_add_token_response, processor_flag, transaction_id)

                        return formatted_response
                except Exception as e:
                    print(f"Error with Payroc processor: {e}")
                    return {
                        'message':f"Error with Payroc processor Exception Hit: {e}",
                        'details': json.dumps({
                            'statusCode': 500,
                            'More details': str(e)
                            })
                    }
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return {
            'message':f"Unexpected Lambda Error Response",
            'details': json.dumps(
                {
                    'statusCode': 500,
                    'LambdaErrorResponse': f"Unexpected error: {e}"
                }
            )
        }
#=========================================
