from moviepy.editor import VideoFileClip

def extract_audio(video_path, audio_path):
    # Загрузка видеофайла
    video = VideoFileClip(video_path)

    # Извлечение аудиодорожки
    audio = video.audio

    # Сохранение аудиодорожки в файл
    audio.write_audiofile(audio_path)

    # Закрытие видеофайла
    video.close()

# Пример использования
video_path = './downloads/tfl_2024_rk1_prep.mp4'
audio_path = './downloads/tfl_2024_rk1_prep.mp3'
extract_audio(video_path, audio_path)
