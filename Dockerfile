FROM archlinux:latest

RUN pacman -Syu --noconfirm && \
    pacman -S --noconfirm python python-pip && \
    pacman -Scc --noconfirm

WORKDIR /app

COPY wiki/requirements.txt .
RUN pip install --break-system-packages -r requirements.txt

COPY wiki/ .

EXPOSE 5000

CMD ["python", "server.py"]
