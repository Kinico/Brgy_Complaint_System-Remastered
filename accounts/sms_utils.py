# accounts/sms_utils.py
import re
from django.core.mail import send_mail
from django.conf import settings

# Philippine Carrier SMS Gateways (Updated with GOMO)
CARRIER_GATEWAYS = {
    # Globe (including GOMO)
    '917': 'globe.com.ph',
    '905': 'globe.com.ph',
    '906': 'globe.com.ph',
    '915': 'globe.com.ph',
    '916': 'globe.com.ph',
    '926': 'globe.com.ph',
    '927': 'globe.com.ph',
    '935': 'globe.com.ph',
    '936': 'globe.com.ph',
    '937': 'globe.com.ph',
    '975': 'globe.com.ph',
    '976': 'globe.com.ph',
    '977': 'globe.com.ph',
    '978': 'globe.com.ph',
    '979': 'globe.com.ph',
    '994': 'globe.com.ph',
    '995': 'globe.com.ph',
    '996': 'globe.com.ph',
    '997': 'globe.com.ph',
    '973': 'globe.com.ph',   # GOMO prefix
    '974': 'globe.com.ph',   # GOMO prefix
    '975': 'globe.com.ph',   # GOMO prefix
    
    # Smart
    '908': 'smart.com.ph',
    '909': 'smart.com.ph',
    '910': 'smart.com.ph',
    '911': 'smart.com.ph',
    '912': 'smart.com.ph',
    '913': 'smart.com.ph',
    '914': 'smart.com.ph',
    '918': 'smart.com.ph',
    '919': 'smart.com.ph',
    '920': 'smart.com.ph',
    '921': 'smart.com.ph',
    '928': 'smart.com.ph',
    '929': 'smart.com.ph',
    '930': 'smart.com.ph',
    '931': 'smart.com.ph',
    '938': 'smart.com.ph',
    '939': 'smart.com.ph',
    '940': 'smart.com.ph',
    '946': 'smart.com.ph',
    '947': 'smart.com.ph',
    '948': 'smart.com.ph',
    '949': 'smart.com.ph',
    '950': 'smart.com.ph',
    '951': 'smart.com.ph',
    '989': 'smart.com.ph',
    '992': 'smart.com.ph',
    '993': 'smart.com.ph',
    '998': 'smart.com.ph',
    '999': 'smart.com.ph',
    
    # Sun (now Smart)
    '922': 'sun.com.ph',
    '923': 'sun.com.ph',
    '924': 'sun.com.ph',
    '925': 'sun.com.ph',
    '932': 'sun.com.ph',
    '933': 'sun.com.ph',
    '934': 'sun.com.ph',
    '942': 'sun.com.ph',
    '943': 'sun.com.ph',
    '944': 'sun.com.ph',
    '945': 'sun.com.ph',
}

def format_phone_number(phone):
    """
    Format phone number to international format
    Example: 09763110925 -> 639763110925
    """
    # Remove all non-digits
    phone = re.sub(r'\D', '', phone)
    
    # Remove leading 0 if present
    if phone.startswith('0'):
        phone = phone[1:]
    
    # Add 63 if not present
    if not phone.startswith('63'):
        phone = '63' + phone
    
    return phone

def get_carrier_gateway(phone):
    """
    Get SMS gateway email address for the phone number
    Handles Globe, Smart, Sun, GOMO
    """
    # Clean phone number
    phone = format_phone_number(phone)
    
    # Get prefix (first 3 digits after 63)
    prefix = phone[2:5]
    
    # Special handling for GOMO (97 prefixes)
    if prefix.startswith('97'):
        prefix = '97' + prefix[2:]
    
    # Find gateway
    gateway = CARRIER_GATEWAYS.get(prefix)
    
    # Default to Globe if unknown
    if not gateway:
        gateway = 'globe.com.ph'
    
    return f"{phone}@{gateway}"

def send_sms(phone_number, message):
    """
    Send SMS using Email-to-SMS gateway
    Returns (success, gateway_used, error_message)
    """
    try:
        # Format phone and get gateway
        gateway_email = get_carrier_gateway(phone_number)
        
        # Send email to gateway
        send_mail(
            subject='Barangay 11 Notification',
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[gateway_email],
            fail_silently=False,
        )
        
        return True, gateway_email, None
        
    except Exception as e:
        return False, None, str(e)

def send_verification_code(phone_number, code):
    """
    Send verification code SMS
    """
    message = f"""
Barangay 11 Verification Code: {code}

This code expires in 10 minutes.
Do not share this code with anyone.
"""
    
    return send_sms(phone_number, message.strip())