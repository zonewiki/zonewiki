FROM archlinux:latest

RUN pacman -Syu --noconfirm && \
    pacman -S --noconfirm python python-pip && \
    pacman -Scc --noconfirm

WORKDIR /app

COPY src/requirements.txt .
RUN pip install --break-system-packages -r requirements.txt

COPY src/ .

EXPOSE 5000

CMD ["python", "server.py"]
