import streamlit as st
import os
import shutil
import subprocess
import requests  # Added to download soundfont
from pathlib import Path
from pdf2image import convert_from_path
from music21 import converter
from midi2audio import FluidSynth
from pydub import AudioSegment

# --- CONFIGURATION ---
# We use a smaller SoundFont URL to avoid GitHub 100MB limits
SOUNDFONT_URL = "https://raw.githubusercontent.com/musescore/MuseScore/master/share/sound/FluidR3Mono_GM.sf3"
SOUNDFONT_FILE = "FluidR3Mono_GM.sf3"

# --- HELPER: DOWNLOAD SOUNDFONT ---
def download_soundfont():
    if not os.path.exists(SOUNDFONT_FILE):
        with st.spinner(f"Downloading SoundFont (one-time setup)..."):
            response = requests.get(SOUNDFONT_URL)
            with open(SOUNDFONT_FILE, "wb") as f:
                f.write(response.content)

# --- BACKEND LOGIC ---
class MusicOCRConverter:
    def __init__(self):
        download_soundfont()  # Ensure file exists
        self.soundfont_path = SOUNDFONT_FILE

    def convert_pdf_to_img(self, pdf_bytes, temp_dir):
        pdf_path = os.path.join(temp_dir, "input.pdf")
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
        images = convert_from_path(pdf_path, first_page=1, last_page=1)
        img_path = os.path.join(temp_dir, "score.png")
        images[0].save(img_path, 'PNG')
        return img_path

    def save_uploaded_image(self, img_bytes, temp_dir):
        img_path = os.path.join(temp_dir, "score.png")
        with open(img_path, "wb") as f:
            f.write(img_bytes)
        return img_path

    def run_omr(self, image_path):
        subprocess.run(["oemer", image_path], check=True)
        potential_files = [image_path + ".musicxml", os.path.splitext(image_path)[0] + ".musicxml"]
        for f in potential_files:
            if os.path.exists(f):
                return f
        raise FileNotFoundError("OMR failed to generate MusicXML.")

    def xml_to_midi(self, xml_path):
        s = converter.parse(xml_path)
        midi_path = xml_path.replace(".musicxml", ".mid")
        s.write('midi', fp=midi_path)
        return midi_path

    def midi_to_mp3(self, midi_path):
        wav_path = midi_path.replace(".mid", ".wav")
        mp3_path = midi_path.replace(".mid", ".mp3")
        fs = FluidSynth(self.soundfont_path)
        fs.midi_to_audio(midi_path, wav_path)
        audio = AudioSegment.from_wav(wav_path)
        audio.export(mp3_path, format="mp3")
        return mp3_path

# --- FRONTEND ---
st.set_page_config(page_title="AI Music Score Player", page_icon="🎵")
st.title("🎵 AI Sheet Music Player")
st.write("Upload a PDF or Image of a music score.")

uploaded_file = st.file_uploader("Choose a music score", type=["pdf", "png", "jpg"])

if uploaded_file is not None:
    temp_dir = "temp_processing"
    os.makedirs(temp_dir, exist_ok=True)
    st.image(uploaded_file, caption="Uploaded Score", use_container_width=True)
    
    if st.button("▶️ Convert to Audio"):
        try:
            converter = MusicOCRConverter()
            with st.spinner("Processing... (AI is reading the notes)"):
                if uploaded_file.name.endswith(".pdf"):
                    img_path = converter.convert_pdf_to_img(uploaded_file.getvalue(), temp_dir)
                else:
                    img_path = converter.save_uploaded_image(uploaded_file.getvalue(), temp_dir)
                
                xml_path = converter.run_omr(img_path)
                midi_path = converter.xml_to_midi(xml_path)
                mp3_path = converter.midi_to_mp3(midi_path)
                
            st.success("Conversion Complete!")
            st.subheader("🎧 Listen")
            with open(mp3_path, "rb") as audio_file:
                st.audio(audio_file.read(), format="audio/mp3")
        except Exception as e:
            st.error(f"Error: {e}")
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
