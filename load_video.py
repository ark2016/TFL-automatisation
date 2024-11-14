import os
import requests

def download_files(file_urls, download_folder):
    """
    Скачивает файлы по указанным ссылкам и сохраняет их в указанную папку.

    :param file_urls: Список URL-адресов файлов для скачивания.
    :param download_folder: Папка для сохранения скачанных файлов.
    """
    # Создаем папку для сохранения файлов, если она не существует
    if not os.path.exists(download_folder):
        os.makedirs(download_folder)

    for file_url in file_urls:
        # Извлекаем имя файла из URL
        file_name = os.path.basename(file_url)

        # Локальный путь для сохранения файла
        local_file_path = os.path.join(download_folder, file_name)

        # Отправка GET-запроса для получения содержимого файла
        response = requests.get(file_url)

        # Проверка успешности запроса
        if response.status_code == 200:
            # Запись содержимого файла в локальный файл
            with open(local_file_path, 'wb') as file:
                file.write(response.content)
            print(f"Файл успешно скачан и сохранен как {local_file_path}")
        else:
            print(f"Ошибка при скачивании файла {file_url}: {response.status_code}")

# Пример использования функции
file_urls = [
    "https://disk.yandex.ru/5f488f18-bb94-43ea-93f8-95b04428db23"
    # "https://disk.yandex.ru/d/Y9TgsNqfZKJcUg/%D0%A1%D0%B5%D0%BC%D0%B5%D1%81%D1%82%D1%80%205/%D0%A2%D0%A4%D0%AF/%D0%92%D0%B8%D0%B4%D0%B5%D0%BE%D0%BC%D0%B0%D1%82%D0%B5%D1%80%D0%B8%D0%B0%D0%BB%D1%8B%20(2024)/tfl_2024_lec_01.mp4",
    # "https://disk.yandex.ru/d/Y9TgsNqfZKJcUg/%D0%A1%D0%B5%D0%BC%D0%B5%D1%81%D1%82%D1%80%205/%D0%A2%D0%A4%D0%AF/%D0%92%D0%B8%D0%B4%D0%B5%D0%BE%D0%BC%D0%B0%D1%82%D0%B5%D1%80%D0%B8%D0%B0%D0%BB%D1%8B%20(2024)/tfl_2024_lec_02.mp4",
    # "https://disk.yandex.ru/d/Y9TgsNqfZKJcUg/%D0%A1%D0%B5%D0%BC%D0%B5%D1%81%D1%82%D1%80%205/%D0%A2%D0%A4%D0%AF/%D0%92%D0%B8%D0%B4%D0%B5%D0%BE%D0%BC%D0%B0%D1%82%D0%B5%D1%80%D0%B8%D0%B0%D0%BB%D1%8B%20(2024)/tfl_2024_lec_03.mp4",
    # "https://disk.yandex.ru/d/Y9TgsNqfZKJcUg/%D0%A1%D0%B5%D0%BC%D0%B5%D1%81%D1%82%D1%80%205/%D0%A2%D0%A4%D0%AF/%D0%92%D0%B8%D0%B4%D0%B5%D0%BE%D0%BC%D0%B0%D1%82%D0%B5%D1%80%D0%B8%D0%B0%D0%BB%D1%8B%20(2024)/tfl_2024_lec_04.mp4",
    # "https://disk.yandex.ru/d/Y9TgsNqfZKJcUg/%D0%A1%D0%B5%D0%BC%D0%B5%D1%81%D1%82%D1%80%205/%D0%A2%D0%A4%D0%AF/%D0%92%D0%B8%D0%B4%D0%B5%D0%BE%D0%BC%D0%B0%D1%82%D0%B5%D1%80%D0%B8%D0%B0%D0%BB%D1%8B%20(2024)/tfl_2024_lec_05.mp4",
    # "https://disk.yandex.ru/d/Y9TgsNqfZKJcUg/%D0%A1%D0%B5%D0%BC%D0%B5%D1%81%D1%82%D1%80%205/%D0%A2%D0%A4%D0%AF/%D0%92%D0%B8%D0%B4%D0%B5%D0%BE%D0%BC%D0%B0%D1%82%D0%B5%D1%80%D0%B8%D0%B0%D0%BB%D1%8B%20(2024)/tfl_2024_lec_06.mp4",
    # "https://disk.yandex.ru/d/Y9TgsNqfZKJcUg/%D0%A1%D0%B5%D0%BC%D0%B5%D1%81%D1%82%D1%80%205/%D0%A2%D0%A4%D0%AF/%D0%92%D0%B8%D0%B4%D0%B5%D0%BE%D0%BC%D0%B0%D1%82%D0%B5%D1%80%D0%B8%D0%B0%D0%BB%D1%8B%20(2024)/tfl_2024_lec_07.mp4",
    # "https://disk.yandex.ru/d/Y9TgsNqfZKJcUg/%D0%A1%D0%B5%D0%BC%D0%B5%D1%81%D1%82%D1%80%205/%D0%A2%D0%A4%D0%AF/%D0%92%D0%B8%D0%B4%D0%B5%D0%BE%D0%BC%D0%B0%D1%82%D0%B5%D1%80%D0%B8%D0%B0%D0%BB%D1%8B%20(2024)/tfl_2024_lec_08.mp4",
    # "https://disk.yandex.ru/d/Y9TgsNqfZKJcUg/%D0%A1%D0%B5%D0%BC%D0%B5%D1%81%D1%82%D1%80%205/%D0%A2%D0%A4%D0%AF/%D0%92%D0%B8%D0%B4%D0%B5%D0%BE%D0%BC%D0%B0%D1%82%D0%B5%D1%80%D0%B8%D0%B0%D0%BB%D1%8B%20(2024)/tfl_2024_lec_09.mp4",
]
download_folder = "downloads"

download_files(file_urls, download_folder)

