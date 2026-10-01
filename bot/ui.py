"""Helpers d'interface Telegram partagés : boutons, affichage d'un écran, suivi du « panneau » courant."""
import html
from typing import Dict

from telegram import InlineKeyboardButton, InlineKeyboardMarkup as M, Update
from telegram.error import BadRequest, TelegramError

E = html.escape
MODE_LABEL = {"demo": "🟠 DÉMO", "real": "🟢 RÉEL"}

PANELS: Dict[int, object] = {}        # chat_id -> dernier écran affiché (supprimé quand on en ouvre un nouveau par texte)


def B(text: str, data: str) -> InlineKeyboardButton:
    """Bouton à callback (le 2e argument positionnel de InlineKeyboardButton est `url`, d'où le mot-clé)."""
    return InlineKeyboardButton(text, callback_data=data)


def U(text: str, url: str) -> InlineKeyboardButton:
    """Bouton-lien (ouvre une URL)."""
    return InlineKeyboardButton(text, url=url)


async def show(update: Update, text: str, rows):
    """Affiche un écran : modifie le message du bouton tapé, ou en envoie un nouveau. Retourne le message."""
    q = update.callback_query
    markup = M(rows)
    chat = update.effective_chat
    try:
        if q:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
            msg = q.message
        else:
            msg = await chat.send_message(text, parse_mode="HTML", reply_markup=markup)
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return q.message if q else None
        msg = await chat.send_message(text, parse_mode="HTML", reply_markup=markup)
    if msg is not None:
        PANELS[chat.id] = msg
    return msg


async def drop(msg) -> bool:
    """Supprime un message sans jamais échouer (déjà supprimé, trop ancien…). True si la suppression a réussi."""
    if msg is None:
        return False
    try:
        await msg.delete()
        return True
    except (TelegramError, AttributeError):
        return False


async def drop_panel(chat_id: int) -> None:
    await drop(PANELS.pop(chat_id, None))
