#!/usr/bin/env python
import sys
import os
import time
import signal
import Queue
import ConfigParser
import socket
import threading
import traceback
from itertools import imap


def plog(*args):
    ts = time.strftime("[%d/%m/%Y-%H:%M:%S]")        
    sys.stderr.write(ts + " " + " ".join(imap(str, args)) + "\n")
    sys.stderr.flush()


class Peer(threading.Thread):

    def __init__(self, peer_manager, connection, address):
        threading.Thread.__init__(self)
        self.peer_manager = peer_manager
        self._stopped = False
        self.lock = threading.Lock()
        self._connection = connection
        self.address = address[0] + ":%d"%address[1]
        self.response_queue = Queue.Queue()
        self._hello_received = False
        self._hello_sent = False


    def connection(self):
        if self.stopped():
            raise Exception("Connection was stopped")
        else:
            return self._connection

    def stop(self):
        with self.lock:
            if self._stopped:
                return
            self._stopped = True
        self.shutdown()


    def stopped(self):
        with self.lock:
            return self._stopped


    def shutdown(self):
        try:
            self._connection.shutdown(socket.SHUT_RDWR)
        except:
            plog(self, "problem shutting down", self.address)
            traceback.print_exc(file=sys.stdout)
            pass
        self._connection.close()

    def send(self, response):
        self.response_queue.put(response)

    def send_hello(self):
        self.response_queue.put('magic:hello:v1')
        self._hello_sent = True


    def run(self):
        while not self.stopped():
            #plog(self, 'in run')
            # send response
            try:
                smessage = self.response_queue.get(timeout=.1)
            except Queue.Empty:
                smessage = None
            
            while smessage:
                plog(self, 'send message', smessage)
                n = self.connection().send(smessage)
                smessage = smessage[n:]
            
            # receive incoming
            #plog(self, 'receive')
            rmessage = self.receive()
        
            if rmessage:
                plog(self, 'received message', rmessage)
                self.parse_message(rmessage)
        
            if not rmessage or smessage:
                time.sleep(0.1)



    def receive(self):
        try:
            return self.connection().recv(2048)
        except:
            return ''

    def parse_message(self, message):

        if not message.startswith('magic'):
            self.response_queue.put('magic:error:wrong header')
            return
        try:
            magic, cmd, data = message.split(':',2)
        except ValueError:
            self.response_queue.put('magic:error:unknown cmd')
            return

        plog(self, 'parsed message', magic, cmd, data)

        if cmd == 'hello':
            self._hello_received = True
            if not self._hello_sent:
                self.send_hello()

        elif cmd == 'ping':
            self.response_queue.put('magic:pong:%d' %0)
        
        elif cmd == 'pong':
            self.response_queue.put('magic:pong:%d' %(int(data)+1))
        
        elif cmd == 'error':
            pass
        else:
            self.response_queue.put('magic:error:unknown cmd')


class PeerManager(threading.Thread):
    
    def __init__(self):
        threading.Thread.__init__(self)
        self._peers = set()
        self._stopped = False
        self.lock = threading.Lock()
        
    def stop(self):
        with self.lock:
            if not self._stopped:
                for peer in self._peers:
                    peer.stop()
            self._stopped = True
    
    def stopped(self):
        with self.lock:
            return self._stopped    

    def add_peer(self, peer):
        with self.lock:
            self._peers.add(peer)

    def remove_peer(self, peer):
        peer.stop()
        with self.lock:
            self._peers.add(peer)

    def connect_peer(self, host, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1)
        plog(self, 'connecting', host, port)
        try:
            sock.connect((host, port))
        except Exception, e:
            plog(self, 'failed', e)
            return False
        sock.settimeout(.1)
        plog(self, 'connected', host, port)
        peer = Peer(self, sock, (host, port))
        self.add_peer(peer)
        peer.send_hello()
        #peer.response_queue.put('magic:pong:1')
        peer.start()
        return True

    def run(self):
        while not self.stopped():
            time.sleep(0.1)


class TcpServer(threading.Thread):

    def __init__(self, peer_manager, host, port):
        self.peer_manager = peer_manager
        threading.Thread.__init__(self)
        self.daemon = True
        self.host = host
        self.port = port
        self.lock = threading.Lock()
        
    def run(self):
        plog(self, "TCP server started on port %d"%self.port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(5)

        while not self.peer_manager.stopped():
            plog(self, 'in run loop')
            try:
                connection, address = sock.accept()
            except:
                traceback.print_exc(file=sys.stdout)
                time.sleep(0.1)
                continue

            connection.settimeout(.1)
            try:
                peer = Peer(self.peer_manager, connection, address)
                self.peer_manager.add_peer(peer)
                peer.start()
                plog(self, "new TCP connection", connection, address)
            except BaseException, e:
                error = str(e)
                plog(self, "cannot start TCP session", error, address)
                connection.close()
                time.sleep(0.1)




def create_config():
    config = ConfigParser.ConfigParser()
    # set some defaults, which will be overwritten by the config file
    config.add_section('server')
    config.set('server', 'host', 'localhost')
    config.set('server', 'port', '30303')
    config.add_section('connect')
    config.set('connect', 'host', '')
    config.set('connect', 'port', '30303')
    config.read([os.path.join(p, '.pyetherum.conf') for p in ('~/', '')])

    if len(sys.argv) > 1:
        config.read(sys.argv[1]) # read optional
        plog('reading config %s' % sys.argv[1])

    return config


def main():
    config = create_config()
    

    peer_manager = PeerManager()
    peer_manager.start()    

    # handle termination signals
    def signal_handler(signum = None, frame = None):
        plog('Signal handler called with signal', signum)
        peer_manager.stop()
    for sig in [signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT, signal.SIGINT]:
        signal.signal(sig, signal_handler)

    # start tcp server
    tcp_server = TcpServer( peer_manager, 
                            config.get('server', 'host'), 
                            config.getint('server', 'port'))
    tcp_server.start()

    # connect peer
    if config.get('connect', 'host'):
        peer_manager.connect_peer(
                        config.get('connect', 'host'), 
                        config.getint('connect', 'port')) 

    # loop
    while not peer_manager.stopped():
        time.sleep(0.1)

    plog('extiting')
    #tcp_server.join() # does not work!
    peer_manager.join()    

if __name__ == '__main__':
    main()
