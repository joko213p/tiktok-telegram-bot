import os
import logging
import asyncio
import yt_dlp
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "METS_TON_TOKEN_ICI")
DOWNLOAD_DIR = "/tmp/tiktok_downloads"
MAX_FILE_SIZE_MB = 50  # Telegram limite les fichiers à 50 Mo (sans serveur premium)
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def is_tiktok_profile_url(url: str) -> bool:
    """Vérifie que l'URL est bien un profil TikTok (pas une vidéo seule)."""
    import re
    # Accepte : https://tiktok.com/@username  ou  https://www.tiktok.com/@username
    pattern = r"https?://(www\.)?tiktok\.com/@[\w.\-]+"
    return bool(re.match(pattern, url.strip()))


def get_video_list(profile_url: str) -> list[dict]:
    """Récupère la liste des vidéos d'un profil sans les télécharger."""
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,   # On récupère juste la liste, pas les fichiers
        "skip_download": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(profile_url, download=False)
        entries = info.get("entries", [])
        return entries


def download_single_video(video_url: str, output_path: str) -> str | None:
    """Télécharge une seule vidéo et retourne le chemin du fichier."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": os.path.join(output_path, "%(id)s.%(ext)s"),
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "max_filesize": MAX_FILE_SIZE_MB * 1024 * 1024,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        filename = ydl.prepare_filename(info)
        # Assure qu'on retourne bien un .mp4
        if not filename.endswith(".mp4"):
            filename = os.path.splitext(filename)[0] + ".mp4"
        if os.path.exists(filename):
            return filename
    return None


# ─── COMMANDES DU BOT ─────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Bonjour ! Je suis ton bot TikTok.\n\n"
        "📌 Envoie-moi le lien d'un profil TikTok et je téléchargerai "
        "toutes ses vidéos pour toi !\n\n"
        "Exemple : https://www.tiktok.com/@nomducompte\n\n"
        "⚠️ Les vidéos lourdes (>50 Mo) seront ignorées automatiquement."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 Aide :\n\n"
        "1️⃣ Copie l'URL d'un profil TikTok\n"
        "   → https://www.tiktok.com/@nomducompte\n\n"
        "2️⃣ Colle-la ici dans le chat\n\n"
        "3️⃣ J'envoie toutes les vidéos une par une 🎬\n\n"
        "⏳ Sois patient, ça peut prendre quelques minutes selon "
        "le nombre de vidéos."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    # Vérifie que c'est bien un lien de profil TikTok
    if not is_tiktok_profile_url(url):
        await update.message.reply_text(
            "❌ Ce lien ne semble pas être un profil TikTok valide.\n\n"
            "Le lien doit ressembler à :\n"
            "https://www.tiktok.com/@nomducompte"
        )
        return

    status_msg = await update.message.reply_text(
        "🔍 Récupération de la liste des vidéos... patiente un instant !"
    )

    try:
        videos = await asyncio.to_thread(get_video_list, url)
    except Exception as e:
        logger.error(f"Erreur lors de la récupération de la liste : {e}")
        await status_msg.edit_text(
            "⚠️ Impossible de récupérer les vidéos de ce profil.\n\n"
            "Vérifie que :\n"
            "• Le profil est public\n"
            "• Le lien est correct\n"
            "• Le compte existe toujours"
        )
        return

    total = len(videos)
    if total == 0:
        await status_msg.edit_text("😕 Aucune vidéo trouvée sur ce profil.")
        return

    await status_msg.edit_text(
        f"✅ {total} vidéo(s) trouvée(s) !\n"
        f"⏬ Téléchargement en cours... (0/{total})"
    )

    # Crée un dossier temporaire unique pour cet utilisateur
    user_folder = os.path.join(DOWNLOAD_DIR, str(update.effective_user.id))
    os.makedirs(user_folder, exist_ok=True)

    sent = 0
    skipped = 0

    for i, video in enumerate(videos, start=1):
        video_url = video.get("url") or video.get("webpage_url")
        if not video_url:
            skipped += 1
            continue

        try:
            file_path = await asyncio.to_thread(
                download_single_video, video_url, user_folder
            )

            if file_path is None:
                skipped += 1
                continue

            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if file_size_mb > MAX_FILE_SIZE_MB:
                os.remove(file_path)
                skipped += 1
                continue

            caption = f"🎬 Vidéo {i}/{total}"
            with open(file_path, "rb") as video_file:
                await update.message.reply_video(video=video_file, caption=caption)

            os.remove(file_path)  # Libère l'espace après envoi
            sent += 1

            # Met à jour le statut toutes les 5 vidéos
            if i % 5 == 0 or i == total:
                await status_msg.edit_text(
                    f"⏬ Progression : {i}/{total} vidéo(s) traitées\n"
                    f"✅ Envoyées : {sent} | ⏭️ Ignorées : {skipped}"
                )

        except Exception as e:
            logger.error(f"Erreur vidéo {i}: {e}")
            skipped += 1
            continue

    # Nettoyage du dossier temporaire
    try:
        for f in os.listdir(user_folder):
            os.remove(os.path.join(user_folder, f))
        os.rmdir(user_folder)
    except Exception:
        pass

    await status_msg.edit_text(
        f"🎉 Terminé !\n\n"
        f"✅ Vidéos envoyées : {sent}\n"
        f"⏭️ Ignorées (trop lourdes ou indisponibles) : {skipped}\n"
        f"📦 Total du profil : {total}"
    )


# ─── DÉMARRAGE DU BOT ──────────────────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot démarré ✅")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
