from transformers import pipeline
import torch

def transcribe_audio(audio_path, output_text_path):
    # Загрузка модели Whisper
    model_name = "openai/whisper-large-v3-turbo"
    pipe = pipeline("automatic-speech-recognition", model=model_name, device=0 if torch.cuda.is_available() else -1)

    # Пропускаем аудио через модель с включенным параметром return_timestamps
    transcription = pipe(audio_path, return_timestamps=True)

    # Сохраняем распознанный текст в текстовый файл
    with open(output_text_path, 'w') as f:
        f.write(transcription['text'])

def read_text_file(file_path):
    # Открываем и читаем текстовый файл
    with open(file_path, 'r') as f:
        content = f.read()
    return content


audio_path = './downloads/tfl_2024_rk1_prep.mp3'
output_text_path = './downloads/tfl_2024_rk1_prep.txt'

# transcribe_audio(audio_path, output_text_path)

text_content = read_text_file(output_text_path)
print(text_content[:1000])