"""Helpers d'interface Telegram partagés : boutons, affichage d'un écran, suivi du « panneau » courant."""
import html
from typing import Dict

from telegram import InlineKeyboardButton, InlineKeyboardMarkup as M, Update
from telegram.error import BadRequest, TelegramError

import contextvars

import visuals

E = html.escape
MODE_LABEL = {"demo": "🟠 DÉMO", "real": "🟢 RÉEL"}

PANELS: Dict[int, object] = {}        # chat_id -> dernier écran affiché (supprimé quand on en ouvre un nouveau par texte)


def B(text: str, data: str) -> InlineKeyboardButton:
    """Bouton à callback (le 2e argument positionnel de InlineKeyboardButton est `url`, d'où le mot-clé)."""
    return InlineKeyboardButton(text, callback_data=data)


def U(text: str, url: str) -> InlineKeyboardButton:
    """Bouton-lien (ouvre une URL)."""
    return InlineKeyboardButton(text, url=url)


CAPTION_MAX = 1024            # limite Telegram d'une légende de photo
SECTION = contextvars.ContextVar("section_banner", default="")      # image par défaut de la section en cours (fixée par route())


def _photo_arg(path):
    """file_id déjà connu (pas de renvoi du fichier) ou fichier à téléverser."""
    fid = visuals.FILE_IDS.get(str(path))
    return fid if fid else open(path, "rb")


def _remember(path, msg) -> None:
    try:
        if msg is not None and getattr(msg, "photo", None):
            visuals.FILE_IDS[str(path)] = msg.photo[-1].file_id
    except (AttributeError, IndexError):
        pass


async def send_photo_path(chat, path, caption: str = "", markup=None):
    """Envoie une image du disque (ou son file_id mémorisé). Retourne le message ou None."""
    arg = _photo_arg(path)
    try:
        msg = await chat.send_photo(arg, caption=caption or None, parse_mode="HTML" if caption else None, reply_markup=markup)
    except TelegramError:
        if isinstance(arg, str):                                  # file_id périmé : on renvoie le fichier
            visuals.FILE_IDS.pop(str(path), None)
            with open(path, "rb") as f:
                msg = await chat.send_photo(f, caption=caption or None, parse_mode="HTML" if caption else None,
                                            reply_markup=markup)
        else:
            raise
    finally:
        if not isinstance(arg, str):
            arg.close()
    _remember(path, msg)
    return msg


async def show(update: Update, text: str, rows, banner=None):
    """Affiche un écran : modifie le message du bouton tapé, ou en envoie un nouveau. Retourne le message.

    banner : nom de l'image d'écran affichée au-dessus du texte (légende de la photo) ; None = celle de la section en cours.
    Telegram ne sait pas transformer un message texte en photo (ni l'inverse) : dans ce cas l'ancien message est
    remplacé. Sans bannière, ou si le texte dépasse la limite d'une légende, l'écran reste un message texte.
    """
    q = update.callback_query
    markup = M(rows)
    chat = update.effective_chat
    if banner is None:                                         # None = image de la section ; "" = aucune image
        banner = SECTION.get()
    path = visuals.banner_path(banner) if banner else None
    if path is not None and len(text) > CAPTION_MAX:
        path = None
    cur = q.message if q else None
    cur_photo = bool(cur is not None and getattr(cur, "photo", None))
    msg = None
    try:
        if path is not None:
            if q and cur_photo:
                try:
                    from telegram import InputMediaPhoto
                    arg = _photo_arg(path)
                    try:
                        res = await q.edit_message_media(InputMediaPhoto(arg, caption=text, parse_mode="HTML"),
                                                         reply_markup=markup)
                    finally:
                        if not isinstance(arg, str):
                            arg.close()
                    msg = res if hasattr(res, "message_id") else cur
                    _remember(path, msg)
                except BadRequest as e:
                    if "not modified" not in str(e).lower():
                        raise
                    msg = cur
            else:
                msg = await send_photo_path(chat, path, text, markup)
                if q:
                    await drop(cur)
        elif q and cur_photo:
            msg = await chat.send_message(text, parse_mode="HTML", reply_markup=markup)
            await drop(cur)
        elif q:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
            msg = cur
        else:
            msg = await chat.send_message(text, parse_mode="HTML", reply_markup=markup)
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return cur if q else None
        if path is not None:                                   # bannière refusée : même écran, sans image
            return await show(update, text, rows, banner="")
        msg = await chat.send_message(text, parse_mode="HTML", reply_markup=markup)
    except (TelegramError, AttributeError, OSError):
        if path is not None:                                   # photo impossible : l'écran reste utilisable en texte seul
            return await show(update, text, rows, banner="")
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


async def ask(update: Update, ctx, text: str, rows, aw: tuple, banner=None):
    """Demande une saisie : le prompt est mémorisé pour être supprimé (ainsi que la réponse) une fois traité."""
    ctx.user_data["await"] = aw
    ctx.user_data["prompt_text"], ctx.user_data["prompt_rows"] = text, rows
    ctx.user_data["prompt"] = await show(update, text, rows, banner=banner)
