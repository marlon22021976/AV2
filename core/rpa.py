import os
import pyautogui
import re
import smtplib
import time
from threading import Lock
from email.message import EmailMessage

from django.core.exceptions import ImproperlyConfigured

_whatsapp_lock = Lock()


def enviar_email_gmail(destinatario, assunto, corpo):
    remetente = os.environ.get('GMAIL_EMAIL')
    senha = os.environ.get('GMAIL_APP_PASSWORD')
    if not remetente or not senha:
        raise ImproperlyConfigured(
            'Configure GMAIL_EMAIL e GMAIL_APP_PASSWORD (senha de app do Gmail).'
        )

    email = EmailMessage()
    email['From'] = remetente
    email['To'] = destinatario
    email['Subject'] = assunto
    email.set_content(corpo)

    with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(remetente, senha)
        smtp.send_message(email)


def enviar_whatsapp(numero, mensagem):
    numero = re.sub(r'[\s().-]', '', numero)
    if not re.fullmatch(r'\+?[1-9]\d{7,14}', numero):
        raise ValueError('O número de WhatsApp deve estar no formato internacional.')

    try:
        import pywhatkit
    except ImportError as exc:
        raise ImproperlyConfigured(
            'A dependência pywhatkit não está instalada. Instale-a para habilitar o WhatsApp Web.'
        ) from exc

    with _whatsapp_lock:
        pywhatkit.sendwhatmsg_instantly(
            phone_no=numero if numero.startswith('+') else f'+{numero}',
            message=mensagem,
            wait_time=12,
            tab_close=False,
        )
        time.sleep(3)
        pyautogui.press('enter')
