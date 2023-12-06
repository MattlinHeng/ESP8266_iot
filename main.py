from machine import UART, reset
from network import WLAN,STA_IF
from urequests import post,get
from time import ticks_ms,ticks_diff,sleep
from gc import collect
import os
from json import loads
from random import getrandbits
from micropython import const
import usocket
characters  = const(0123456789)
class UARTReader:
    def __init__(self, uart, buffer_size=1024, eol='\n'):
        self.uart = uart
        self.buffer_size = buffer_size
        collect()
        self.buffer = bytearray(buffer_size)
        self.position = 0
        self.eol = eol.encode('utf-8')
    def readline(self, timeout=100):
        start_time = ticks_ms()
        while ticks_diff(ticks_ms(), start_time) < timeout:
            if self.uart.any():
                start_time = ticks_ms()
                byte = self.uart.read(1)
                if byte:
                    self.buffer[self.position:self.position + 1] = byte
                    self.position += 1
                    if byte == self.eol:
                        return self.readall()
        return self.readall()
    def readall(self):
        data = self.buffer[:self.position]
        self.position = 0
        return data.decode('utf-8').strip()
    def clear_buffer(self):
        self.buffer = bytearray(self.buffer_size)
        self.position = 0
class NetworkManager:
    def __init__(self, uart):
        self.uart = uart
        self.wifi = WLAN(STA_IF)
        self.uart_reader = UARTReader(self.uart)
    def download_and_replace_script(self,url, filename):
        response = get(url)
        if response.status_code == 200:
            with open(filename, 'w') as file:
                file.write(response.text)
            print("OTA_done.")
        else:
            print("OTA_Fail")
        response.close()
    def apply_update(self,url):
        self.download_and_replace_script(url,'main.py')
        reset()
    def save_file_from_uart_and_upload(self, url, file_name,headers=None):
        start_marker = "<START_OF_FILE>"
        end_marker = "<END_OF_FILE>"
        timeout = 2000
        last_data_time = ticks_ms()
        file_received = False
        data_len = 0
        with open(file_name, 'wb') as file:
            self.uart.write(b'Ready to receive file\n')
            while True:
                file_chunk = self.uart.readline()
                if file_chunk is None:
                    if ticks_diff(ticks_ms(), last_data_time) > timeout:
                        self.uart.write("Error: start upload nothing over 2 seconds\n".encode('utf-8'))
                        return
                    sleep(0.1)
                    continue
                elif start_marker in file_chunk:
                    last_data_time = ticks_ms()  
                    while True:
                        file_chunk = self.uart.readline()
                        if file_chunk is None:
                            if ticks_diff(ticks_ms(), last_data_time) > timeout:
                                self.uart.write("Error: end upload nothing over 2 seconds\n".encode('utf-8'))
                                return
                            sleep(0.1)
                            continue
                        elif end_marker in file_chunk:
                            end_index = file_chunk.find(end_marker.encode('utf-8'))
                            if end_index >= 0:
                                file.write(file_chunk[:end_index])
                                data_len += end_index-1
                            file_received = True
                            self.uart.write(f"Total saved {data_len} bytes.\n".encode('utf-8'))  
                            break 
                        last_data_time = ticks_ms() 
                        file.write(file_chunk)
                        data_len += len(file_chunk)
                    break
        del start_marker,end_marker
        del timeout,last_data_time
        if file_received:
            self.upload_file(url, file_name)  
        else:
            self.uart.write("UPLOAD_FAIL\n".encode('utf-8'))
            try:
                os.remove(file_name)
            except:
                pass
    def scan_wifi(self):
        self.wifi.active(True)
        networks = self.wifi.scan()
        for net in networks:
            ssid = net[0].decode('utf-8')
            bssid = ':'.join('%02x' % b for b in net[1])
            channel = net[2]
            RSSI = net[3]
            authmode = net[4]
            hidden = net[5]
            self.uart.write("SSID: {}, BSSID: {}, Channel: {}, RSSI: {}, Authmode: {}, Hidden: {}\n".format(
                ssid, bssid, channel, RSSI, authmode, hidden).encode('utf-8'))
    def send_post_request(self, url, data, headers=None):
        if headers is None:
            headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        else:
            try:
                headers = loads(headers)
            except ValueError as e:
                headers = {
                    'Content-Type': 'application/x-www-form-urlencoded'
                } 
        response = post(url, data=data, headers=headers)
        return response.text
    def send_get_request(self, url):
        text = None
        try:
            response = get(url)
            text = response.text
        except Exception as e:
            self.uart.write("Error: {}\n".format(str(e)).encode('utf-8'))
            return None
        finally:
            if 'response' in locals():
                response.close()
        return text
    def download_file_to_uart(self, url, post_data, headers=None):
        if headers is None:
            headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        else:
            try:
                headers = loads(headers)
            except ValueError as e:
                headers = {
                    'Content-Type': 'application/x-www-form-urlencoded'
                } 
        try:
            response = post(url, data=post_data, headers=headers, stream=True)  
            collect()
            if response.status_code == 200:
                while True:
                    chunk = response.raw.read(1024) 
                    if chunk:
                        self.uart.write(chunk)
                        collect()
                    else:
                        break  
                self.uart.write(f'\nFile download completed!\n'.encode('utf-8'))
            else:
                self.uart.write(f'Failed to download file: {response.status_code}\n'.encode('utf-8'))
        except Exception as e:
            self.uart.write(f'Error: {str(e)}\n'.encode('utf-8'))
        finally:
            if 'response' in locals():
                response.close()
    def upload_file_part(self, url, part_filename, chunk, headers):
        try:
            response = post(url, data=chunk, headers=headers)
            if response.status_code == 200:
                self.uart.write(f'{part_filename}_UPLOAD: {response.text}\n'.encode('utf-8'))
            else:
                self.uart.write(f'{part_filename}_UPLOAD_FAIL: {response.status_code}\n'.encode('utf-8'))
        except Exception as e:
            self.uart.write(f'Error: {str(e)}\n'.encode('utf-8'))
        finally:
            if 'response' in locals():
                response.close()
    def upload_file(self, url, filename):
        CHUNK_SIZE = 512  
        part_number = 1
        collect()
        headers = {}
        char_len = len(characters)
        boundary = ''
        for _ in range(24):
            rand_bits = getrandbits(6)  
            index = rand_bits % char_len 
            boundary += characters[index]
        boundary = '----------' + boundary
        headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
        import uio
        try:
            with open(filename, 'rb') as file:
                while True:
                    part_filename = f"{filename}-{part_number}"
                    chunk = file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    with uio.BytesIO() as buf:
                        buf.write(f'--{boundary}\r\n'.encode('utf-8'))
                        buf.write(f'Content-Disposition: form-data; name="Sunset_Palmtree"; filename="{part_filename}"\r\n'.encode('utf-8'))
                        buf.write('Content-Type: application/octet-stream\r\n\r\n'.encode('utf-8'))
                        buf.write(chunk)
                        buf.write(f'\r\n--{boundary}--\r\n'.encode('utf-8'))
                        content_length = buf.tell()
                        headers['Content-Length'] = str(content_length)
                        buf.seek(0)
                        self.upload_file_part(url, part_filename, buf.getvalue(), headers)
                        part_number += 1
        except Exception as e:
            self.uart.write(f'Error: {str(e)}\n'.encode('utf-8'))
        finally:
            del uio
            try:
                os.remove(filename)
            except:
                self.uart.write(f'Error: {str(e)}\n'.encode('utf-8'))
    def handle_uart_commands(self):
        self.socket = None
        while True:
            try:
                received_data = self.uart_reader.readline()
                if received_data:
                    cmdl = received_data.split(':')
                    command = cmdl[0]
                    if len(cmdl) > 1:
                        get_url = ':'.join(cmdl[1:]) if len(cmdl) > 1 else None
                        params = get_url.split(',') if len(cmdl) > 1 else None
                        if command in ["UPLOAD", "DOWNLOAD", "POST"] and len(params) < 2:
                            self.uart.write("Error: Not enough parameters for command\n".encode('utf-8'))
                        else:
                            if len(params) == 2:
                                params.append(None)  
                        args = cmdl[1] if len(cmdl) > 1 else None
                    else:
                        command = received_data
                        args = None
                        get_url = None
                        params = None
                    if command == "WIFI_CONNECT" and args:
                        ssid, password = args.split(',')
                        self.wifi.active(True)
                        self.wifi.connect(ssid, password)
                        start_time = ticks_ms()
                        while not self.wifi.isconnected():
                            if ticks_diff(ticks_ms(), start_time) > 8000:
                                self.uart.write("Wifi connection timed out\n".encode('utf-8'))
                                break
                        if self.wifi.isconnected():
                            self.uart.write("Connecting Wifi successfully!\n".encode('utf-8'))
                    elif command == "WIFI_RESET":
                        reset()
                    elif command == "ISCONNECT":
                        self.uart.write(f"{self.wifi.isconnected()}\n".encode('utf-8'))
                    elif command == "IFCONFIG":
                        self.uart.write(f"{self.wifi.ifconfig()}\n".encode('utf-8'))
                    elif command == "GET_MAC":
                        if not self.wifi.active():
                            self.wifi.active(True)
                        mac_address = self.wifi.config('mac')
                        mac_address_str = ':'.join('{:02x}'.format(b) for b in mac_address)
                        self.uart.write((mac_address_str+'\n').encode('utf-8'))
                    elif command =="DOWNLOAD" and params:
                        self.download_file_to_uart(params[0],params[1],params[2])
                    elif command == "UPLOAD" and params:
                        self.save_file_from_uart_and_upload(params[0],params[1])
                    elif command == "POST" and params:
                        response_text = self.send_post_request(params[0],params[1],params[2])
                        self.uart.write(response_text.encode('utf-8'))
                    elif command == "GET" and get_url:
                        response_text = self.send_get_request(get_url)
                        self.uart.write(response_text.encode('utf-8'))
                    elif command == "OTA" and get_url:
                        response_text = self.apply_update(get_url)
                    elif command == "SCAN_WIFI":
                        self.scan_wifi()
                    elif command == "SOCKET_CONNECT" and len(cmdl) > 2:
                        host, port = cmdl[1], int(cmdl[2])
                        self.socket = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
                        self.socket.connect((host, port))
                        self.uart.write("Socket connected\n".encode('utf-8'))
                    elif command == "SOCKET_SEND" and len(cmdl) > 1:
                        data = cmdl[1]
                        self.socket.send(data.encode('utf-8'))
                        self.uart.write("Data sent\n".encode('utf-8'))
                    elif command == "SOCKET_RECEIVE":
                        data = self.socket.recv(1024)
                        self.uart.write(f"Received: {data}\n".encode('utf-8'))
                    elif command == "SOCKET_CLOSE":
                        if self.socket:
                            self.socket.close()
                            self.socket = None
                        self.uart.write("Socket closed\n".encode('utf-8'))
                    self.uart_reader.clear_buffer()
                    collect()
            except Exception as e:
                self.uart.write(f"Error: {str(e)}\n".encode('utf-8'))
if __name__ == '__main__':
    collect()
    uart = UART(0, baudrate=115200,rxbuf=1024) 
    nm = NetworkManager(uart)  
    nm.handle_uart_commands() 