import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import config
from indicators import get_signal
from martingale import MartingaleEngine
from trading_engine import TradingEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# état par chat_id
sessions = {}


def get_session(chat_id):
    if chat_id not in sessions:
        sessions[chat_id] = {
            "mode": "DEMO" if config.PO_DEMO else "RÉEL",
            "stake": config.MONEY["base_stake"],
            "asset": None,
            "auto_mode": None,  # 'SERIES' | 'TAKE_PROFIT'
            "running": False,
            "deals_target": 5,
            "deals_done": 0,
            "martingale": MartingaleEngine(
                config.MONEY["base_stake"],
                config.MONEY["martingale_multiplier"],
                config.MONEY["max_steps"],
            ),
        }
    return sessions[chat_id]


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_main_menu(update.effective_chat.id, context)


async def show_main_menu(chat_id, context):
    s = get_session(chat_id)
    keyboard = [
        [InlineKeyboardButton("🚀 Lancer l'auto-trading", callback_data="menu_autotrading")],
        [InlineKeyboardButton("🎮 Mode manuel", callback_data="menu_manual")],
    ]
    await context.bot.send_message(
        chat_id,
        f"🟠 Mode : *{s['mode']}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def ask_stake(chat_id, context):
    s = get_session(chat_id)
    keyboard = [
        [
            InlineKeyboardButton("$1", callback_data="stake_1"),
            InlineKeyboardButton("$2", callback_data="stake_2"),
            InlineKeyboardButton("$5", callback_data="stake_5"),
            InlineKeyboardButton("$10", callback_data="stake_10"),
        ]
    ]
    await context.bot.send_message(
        chat_id,
        f"Choisissez votre mise de départ :\n\nLa session tournera sur : *{s['mode']}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def ask_asset(chat_id, context):
    keyboard = [
        [InlineKeyboardButton("🎯 Sélection auto", callback_data="asset_auto")],
        [InlineKeyboardButton("🔧 Choisir manuellement", callback_data="asset_manual")],
        [InlineKeyboardButton("⬅️ Menu", callback_data="menu_main")],
    ]
    await context.bot.send_message(
        chat_id,
        "Choisir l'actif automatiquement (le bot prend la paire au meilleur "
        "paiement et signal) ou manuellement ?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def ask_auto_mode(chat_id, context):
    keyboard = [
        [InlineKeyboardButton("🎯 Série (5 deals)", callback_data="mode_series")],
        [InlineKeyboardButton("📈 Par take-profit", callback_data="mode_takeprofit")],
        [InlineKeyboardButton("⬅️ Menu", callback_data="menu_main")],
    ]
    await context.bot.send_message(
        chat_id,
        "🎯 *Série de 5 deals* — 5 deals d'affilée avec martingale, un deal "
        "se ferme dès qu'il est en profit.\n\n"
        "📈 *Par take-profit* — trade jusqu'à un profit ou un stop-loss défini.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    s = get_session(chat_id)
    data = query.data
    await query.answer()

    if data == "menu_main":
        return await show_main_menu(chat_id, context)
    if data == "menu_autotrading":
        return await ask_stake(chat_id, context)
    if data == "menu_manual":
        return await context.bot.send_message(chat_id, "Mode manuel : à implémenter selon tes besoins.")

    if data.startswith("stake_"):
        s["stake"] = int(data.split("_")[1])
        s["martingale"] = MartingaleEngine(
            s["stake"], config.MONEY["martingale_multiplier"], config.MONEY["max_steps"]
        )
        return await ask_asset(chat_id, context)

    if data == "asset_auto":
        s["asset"] = "AUTO"
        return await ask_auto_mode(chat_id, context)
    if data == "asset_manual":
        return await context.bot.send_message(chat_id, "Envoie le nom de la paire (ex: EURUSD_otc).")

    if data == "mode_series":
        s["auto_mode"] = "SERIES"
        s["deals_target"] = 5
        return await start_auto_trading(chat_id, context)
    if data == "mode_takeprofit":
        s["auto_mode"] = "TAKE_PROFIT"
        return await context.bot.send_message(chat_id, 'Envoie le take-profit et stop-loss, ex: "+10 -5"')

    if data == "stop":
        s["running"] = False
        return await context.bot.send_message(chat_id, "⏹️ Arrêté.")


async def start_auto_trading(chat_id, context):
    s = get_session(chat_id)
    s["running"] = True
    s["deals_done"] = 0

    keyboard = [[InlineKeyboardButton("⬛ Arrêter", callback_data="stop")]]
    await context.bot.send_message(
        chat_id,
        f"🤖 Auto-trading lancé · mise ${s['stake']} · mode {s['auto_mode']}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    asyncio.create_task(run_trading_loop(chat_id, context))


async def run_trading_loop(chat_id, context):
    s = get_session(chat_id)
    engine: TradingEngine = context.bot_data["engine"]
    symbol = "EURUSD_otc" if s["asset"] == "AUTO" else s["asset"]

    while s["running"]:
        candles = engine.get_candles(symbol)
        signal, rsi = get_signal(candles, config.STRATEGY)

        if not signal:
            await asyncio.sleep(1)
            continue

        stake = s["martingale"].get_current_stake()
        status, trade_id = engine.place_trade(
            symbol, signal, stake, config.STRATEGY["trade_duration_sec"]
        )

        if not status:
            await context.bot.send_message(chat_id, "⚠️ Échec du passage d'ordre, on réessaie.")
            await asyncio.sleep(1)
            continue

        await asyncio.sleep(config.STRATEGY["trade_duration_sec"] + 1)
        won, profit = engine.check_result(trade_id)

        mg_result = s["martingale"].register_result(won)
        emoji = "🟢" if won else "🔴"
        await context.bot.send_message(
            chat_id, f"{emoji} étape {mg_result['step']} · mise ${stake} · résultat {profit}"
        )

        if mg_result["done"]:
            s["deals_done"] += 1
            await context.bot.send_message(
                chat_id,
                f"Deal {s['deals_done']}/{s['deals_target']} clôturé en "
                f"{'profit' if mg_result['won'] else 'perte'}",
            )
            if s["auto_mode"] == "SERIES" and s["deals_done"] >= s["deals_target"]:
                s["running"] = False
                await context.bot.send_message(chat_id, f"🏁 Terminé : {s['deals_done']}/{s['deals_target']} deals")


def main():
    engine = TradingEngine(demo=config.PO_DEMO, ssid=config.PO_SSID)
    engine.connect()

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.bot_data["engine"] = engine
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))

    logger.info("Bot Telegram démarré.")
    app.run_polling()


if __name__ == "__main__":
    main()
