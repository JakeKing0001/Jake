"""Rubrica minima e messaggi (v3.0): contatti salvati nella memoria a lungo termine
(categoria 'contact'), WhatsApp via URI whatsapp:// (app desktop) o wa.me (browser), email
via mailto:. Il messaggio viene PREPARATO nella finestra giusta, mai inviato da solo:
un'ultima occhiata prima di premere invio e' il prezzo giusto per un comando vocale."""
import json
import os
import re
import webbrowser
from urllib import parse

from core.skill_result import SkillResult

CATEGORY = "contact"


def _digits(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if digits and not digits.startswith("00") and len(digits) <= 10:
        digits = "39" + digits  # numero italiano senza prefisso internazionale
    return digits.lstrip("0") if digits.startswith("00") else digits


class ContactBook:
    def __init__(self, memory_manager):
        self.memory_manager = memory_manager

    def save(self, name: str, phone: str = None, email: str = None) -> dict:
        name = name.strip().lower()
        current = self.get(name) or {}
        if phone:
            current["phone"] = phone.strip()
        if email:
            current["email"] = email.strip()
        self.memory_manager.remember(name, json.dumps(current, ensure_ascii=False), category=CATEGORY, importance=2)
        current["name"] = name
        return current

    def get(self, name: str) -> dict | None:
        rows = self.memory_manager.recall(key=name.strip().lower(), category=CATEGORY, limit=1)
        if not rows:
            rows = self.memory_manager.recall(query=name.strip().lower(), category=CATEGORY, limit=1)
        if not rows:
            return None
        try:
            data = json.loads(rows[0]["value"])
        except (json.JSONDecodeError, TypeError):
            data = {}
        data["name"] = rows[0]["key"]
        return data

    def list_all(self) -> list[dict]:
        contacts = []
        for row in self.memory_manager.recall(category=CATEGORY, limit=100):
            try:
                data = json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                data = {}
            data["name"] = row["key"]
            contacts.append(data)
        return contacts


class SaveContactSkill:
    metadata = {
        "intent": "SAVE_CONTACT",
        "description": "Salva in rubrica il numero di telefono e/o l'email di una persona. Usalo per "
        "'salva il numero di Marco: ...', 'il numero di Giulia e' ...', 'salva l'email di Luca'.",
        "parameters": {
            "name": {"type": "string", "required": True, "description": "Nome della persona."},
            "phone": {"type": "string", "required": False, "description": "Numero di telefono, cosi' come detto."},
            "email": {"type": "string", "required": False, "description": "Indirizzo email."},
        },
    }

    def __init__(self, contact_book: ContactBook):
        self.contact_book = contact_book

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        name = (parameters.get("name") or "").strip()
        phone = (parameters.get("phone") or "").strip()
        email = (parameters.get("email") or "").strip()
        if not name or (not phone and not email):
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        contact = self.contact_book.save(name, phone=phone or None, email=email or None)
        return SkillResult(success=True, data=contact)


class ListContactsSkill:
    metadata = {
        "intent": "LIST_CONTACTS",
        "description": "Elenca i contatti salvati in rubrica.",
        "parameters": {},
    }

    def __init__(self, contact_book: ContactBook):
        self.contact_book = contact_book

    def execute(self, parameters: dict = None):
        contacts = self.contact_book.list_all()
        if not contacts:
            return SkillResult(success=False, data={}, error="NOT_FOUND")
        return SkillResult(success=True, data={"contacts": contacts})


class SendWhatsAppSkill:
    metadata = {
        "intent": "SEND_WHATSAPP",
        "description": "Prepara un messaggio WhatsApp a un contatto della rubrica (apre la chat con il "
        "testo gia' scritto, da confermare con invio). Usalo per 'manda un whatsapp a Marco: ...', "
        "'scrivi su whatsapp a Giulia che ...'.",
        "parameters": {
            "contact": {"type": "string", "required": True, "description": "Nome del contatto (o numero)."},
            "message": {"type": "string", "required": True, "description": "Testo del messaggio."},
        },
    }

    def __init__(self, contact_book: ContactBook):
        self.contact_book = contact_book

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        contact_name = (parameters.get("contact") or "").strip()
        message = (parameters.get("message") or "").strip()
        if not contact_name or not message:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")

        if re.search(r"\d{6,}", contact_name):
            phone = contact_name
        else:
            contact = self.contact_book.get(contact_name)
            phone = (contact or {}).get("phone")
            if not phone:
                return SkillResult(success=False, data={"name": contact_name}, error="CONTACT_NOT_FOUND")
        number = _digits(phone)
        text = parse.quote(message)
        try:
            os.startfile(f"whatsapp://send?phone={number}&text={text}")
            return SkillResult(success=True, data={"contact": contact_name, "message": message, "via": "app"})
        except OSError:
            pass
        try:
            webbrowser.open(f"https://wa.me/{number}?text={text}")
        except Exception:
            return SkillResult(success=False, data={"contact": contact_name}, error="OPERATION_FAILED")
        return SkillResult(success=True, data={"contact": contact_name, "message": message, "via": "web"})


class SendEmailSkill:
    metadata = {
        "intent": "SEND_EMAIL",
        "description": "Prepara una email nel programma di posta predefinito (destinatario, oggetto e "
        "testo gia' compilati, da rileggere e inviare). Usalo per 'manda una mail a ...', 'scrivi "
        "un'email a ... per dirgli che ...'.",
        "parameters": {
            "to": {"type": "string", "required": True, "description": "Indirizzo email o nome di un contatto in rubrica."},
            "subject": {"type": "string", "required": False, "description": "Oggetto."},
            "body": {"type": "string", "required": False, "description": "Testo del messaggio."},
        },
    }

    def __init__(self, contact_book: ContactBook):
        self.contact_book = contact_book

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        to = (parameters.get("to") or "").strip()
        subject = (parameters.get("subject") or "").strip()
        body = (parameters.get("body") or "").strip()
        if not to:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        if "@" not in to:
            contact = self.contact_book.get(to)
            email = (contact or {}).get("email")
            if not email:
                return SkillResult(success=False, data={"name": to}, error="CONTACT_NOT_FOUND")
            to = email
        query = parse.urlencode({k: v for k, v in (("subject", subject), ("body", body)) if v}, quote_via=parse.quote)
        url = f"mailto:{to}" + (f"?{query}" if query else "")
        try:
            os.startfile(url)
        except OSError:
            try:
                webbrowser.open(url)
            except Exception:
                return SkillResult(success=False, data={"to": to}, error="OPERATION_FAILED")
        return SkillResult(success=True, data={"to": to, "subject": subject})
