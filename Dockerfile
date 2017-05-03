FROM tensorflow/tensorflow:1.1.0-gpu-py3
RUN apt-get update
RUN apt-get install -y python3-tk
WORKDIR /app
COPY ./requirements.txt /app/requirements.txt
RUN pip install -r ./requirements.txt
COPY ./ /app
RUN chmod +x runs.sh