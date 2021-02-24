import time
import socket
import struct
import select
import random
import asyncore
import sys
import json
from is_wire.core import Channel, Status, StatusCode, Message
from is_wire.rpc import ServiceProvider, LogInterceptor
from nerds.futebol.api_pb2 import PingStatus


ICMP_CODE = socket.getprotobyname('icmp')

class PING:

    def __init__(self, *args, **kwargs):
        self.ICMP_ECHO_REQUEST = 8
        self.icmp_seq = 1
        self.packet_loss = 0
        self.loss_perc = 0
        self.last_seq = 0
        self.delay_list = []
        self.file=open("ping_result.txt","w")
        self.connection = Channel(**kwargs)
        self.status = PingStatus()
        

    def checksum(self,source_string):
        # I'm not too confident that this is right but testing seems to
        # suggest that it gives the same answers as in_cksum in ping.c.
        sum = 0
        count_to = (len(source_string) / 2) * 2
        count = 0
        while count < count_to:
            this_val = ord(source_string[count + 1])*256+ord(source_string[count])
            sum = sum + this_val
            sum = sum & 0xffffffff # Necessary?
            count = count + 2
        if count_to < len(source_string):
            sum = sum + ord(source_string[len(source_string) - 1])
            sum = sum & 0xffffffff # Necessary?
        sum = (sum >> 16) + (sum & 0xffff)
        sum = sum + (sum >> 16)
        answer = ~sum
        answer = answer & 0xffff
        # Swap bytes. Bugger me if I know why.
        answer = answer >> 8 | (answer << 8 & 0xff00)
        return answer


    def create_packet(self,id):
        """Create a new echo request packet based on the given "id"."""
        # Header is type (8), code (8), checksum (16), id (16), sequence (16)
        header = struct.pack('bbHHh', self.ICMP_ECHO_REQUEST, 0, 0, id, self.icmp_seq)
        data = 192 * 'Q'
        # Calculate the checksum on the data and the dummy header.
        my_checksum = self.checksum(header + data)
        # Now that we have the right checksum, we put that in. It's just easier
        # to make up a new header than to stuff it into the dummy.
        header = struct.pack('bbHHh', self.ICMP_ECHO_REQUEST, 0,
                            socket.htons(my_checksum), id, self.icmp_seq)
        return header + data


    def do_one(self,dest_addr, timeout=1):
        """
        Sends one ping to the given "dest_addr" which can be an ip or hostname.
        "timeout" can be any integer or float except negatives and zero.

        Returns either the delay (in seconds) or None on timeout and an invalid
        address, respectively.

        """
        try:
            my_socket = socket.socket(socket.AF_INET, socket.SOCK_RAW, ICMP_CODE)
        except:
            pass            
        try:
            host = socket.gethostbyname(dest_addr)
        except:
            pass
        # Maximum for an unsigned short int c object counts to 65535 so
        # we have to sure that our packet id is not greater than that.
        packet_id = int((id(timeout) * random.random()) % 65535)
        packet = self.create_packet(packet_id)
        while packet:
            # The icmp protocol does not use a port, but the function
            # below expects it, so we just give it a dummy port.
            sent = my_socket.sendto(packet, (dest_addr, 1))
            packet = packet[sent:]
    
        delay = self.receive_ping(my_socket, packet_id, time.time(), timeout)
        my_socket.close()
        return delay


    def receive_ping(self,my_socket, packet_id, time_sent, timeout):
        # Receive the ping from the socket.
        time_left = timeout
        while True:
            started_select = time.time()
            ready = select.select([my_socket], [], [], time_left)
            how_long_in_select = time.time() - started_select
            if ready[0] == []: # Timeout
                return
            time_received = time.time()
            rec_packet, addr = my_socket.recvfrom(1024)
            icmp_header = rec_packet[20:28]
            type, code, checksum, p_id, sequence = struct.unpack(
                'bbHHh', icmp_header)
            
            
            if self.icmp_seq - self.last_seq > 1:
                loss = self.icmp_seq - self.last_seq - 1
                self.packet_loss = self.packet_loss + loss
            
            self.last_seq = self.icmp_seq
                
            if p_id == packet_id:
                return time_received - time_sent
            time_left -= time_received - time_sent
            if time_left <= 0:
                return
    
    
    def ping(self,dest_addr, count, timeout):
        """
        Sends one ping to the given "dest_addr" which can be an ip or hostname.

        "timeout" can be any integer or float except negatives and zero.
        "count" specifies how many pings will be sent.

        Displays the result on the screen.
    
        """
        print('ping {}...'.format(dest_addr))
        for self.icmp_seq in range(count):
            delay = self.do_one(dest_addr, timeout)
            if delay == None:
                print('failed. (Timeout within {} seconds.)'.format(timeout))
            else:
                delay = round(delay * 1000.0, 4)
                print('from {}  icmp_seq = {}   time = {} ms'.format(dest_addr, (self.icmp_seq +1), delay))
                latency = delay
                self.status.delay = latency
                self.connection.publish(topic="Connection.Status", message=Message(content=self.status))
                self.file.write('from {}  icmp_seq = {}   time = {} ms'.format(dest_addr, (self.icmp_seq +1), delay) + '\n')
            
            self.seq_total = self.icmp_seq + 1
            self.delay_list.append(latency)
            time.sleep(1)

        self.loss_perc = self.percent(self.packet_loss, self.seq_total)

        self.min_delay = min(self.delay_list)
        self.max_delay = max(self.delay_list)
        self.avg_delay = round(sum(self.delay_list)/len(self.delay_list),4)
        
        print('{} packets transmitted, {} received, {}% packet loss'.format(self.seq_total, (self.seq_total - self.packet_loss), self.loss_perc))
        print('rtt min/avg/max = {}/{}/{}'.format(self.min_delay, self.avg_delay, self.max_delay))

        self.status.max_delay = self.max_delay
        self.status.min_delay = self.min_delay
        self.status.avg_delay = self.avg_delay
        self.status.pkt_tx = self.seq_total
        self.status.pkt_rx = (self.seq_total - self.packet_loss)
        self.status.pkt_loss = self.packet_loss
        self.status.perc_loss = self.loss_perc

        self.connection.publish(topic="Connection.Status", message=Message(content=self.status))
        
        self.file.write('\n' + '{} packets transmitted, {} received, {}% packet loss'.format(self.seq_total, (self.seq_total - self.packet_loss), self.loss_perc) + '\n')
        self.file.write('rtt(ms) min/avg/max = {}/{}/{}'.format(self.min_delay, self.avg_delay, self.max_delay))
        self.file.close
        
    def percent(self, num1, num2):
        num1 = float(num1)
        num2 = float(num2)
        percentage = '{0:.1f}'.format((num1 / num2 * 100))
        return percentage

def main():
    config_path = "ping_config.json" if len(sys.argv) != 2 else sys.argv[1]
    with open(config_path) as f:
        config = json.load(f)

    ping = PING(config.get("amqp"))    
    ping.ping(config.get("dest_addr"), config.get("count"), config.get("timeout"))

    

if __name__=='__main__':
    main()