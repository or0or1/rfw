import argparse, logging, re, sys
from Queue import Queue
from threading import Thread
import config, cmdparse, cmdexe
from sslserver import SSLServer, BasicAuthRequestHandler

   




def create_requesthandler(rfwconf, cmd_queue):
    """Create RequestHandler type. This is a way to avoid global variables: a closure returning a class type that binds rfwconf inside. 
    """
    class RequestHandler(BasicAuthRequestHandler):
    
        def creds_check(self, user, password):
            return user == rfwconf.auth_username() and password == rfwconf.auth_password()
    
        
        # modify should be 'D' for Delete or 'I' for Insert understood as -D and -I iptables flags
        def add_command(self, modify):
            assert modify == 'D' or modify == 'I'
            print("self.path=" + self.path)
            
            rcmd = cmdparse.parse_command(self.path)

            print("command1: %s" % rcmd) 
            if rcmd.get('error'):
                content = rcmd['error']
                self.send_response(400)  # Bad Request
                self.send_header("Content-Length", len(content) + 2)
                self.end_headers()
                self.wfile.write(content + "\r\n")
                return
                
            
            chain = rcmd['chain']
            if chain == 'input':
                action = rfwconf.chain_input_action()
            elif chain == 'output':
                action = rfwconf.chain_output_action()
            elif chain == 'forward':
                action = rfwconf.chain_forward_action()
            else:
                assert False, "Wrong chain name: {}".format(chain)
                        
            assert action in ['DROP', 'ACCEPT']

            rcmd['action'] = action
            rcmd['modify'] = modify
            
            print("command2: %s" % rcmd) 

            cmd_queue.put_nowait(rcmd)

            content = str(rcmd)
    
            #request content can be read from rfile
            #inp = self.rfile.read(65000) # use Content-Length to know how many bytes to read
            #content = inp + "\r\n" + content
    
            self.send_response(200)
            self.send_header("Content-Length", len(content) + 2)
            self.send_header("Last-Modified", self.date_time_string())
            self.end_headers()
            self.wfile.write(content + "\r\n")

            
    
        def do_PUT(self):
            self.add_command('I')
    
        def do_DELETE(self):
            self.add_command('D')
    
        def do_GET(self):
            if rfwconf.is_non_restful(): 
                #TODO here it will be more complicated. The GET listing requests are valid in restful scenario
                self.do_POST()
            else:
                self.send_response(405) # Method Not Allowed
    
        def do_POST(self):
            self.add_command('I')

    return RequestHandler



def parse_commandline():
    parser = argparse.ArgumentParser(description='rfw - Remote Firewall')
    parser.add_argument('-f', '--configfile', default='/etc/rfw/rfw.conf', help='rfw config file (default /etc/rfw/rfw.conf)')
    args = parser.parse_args()
    return args.configfile


def ip2long(s):
    """Convert IP address string to big-endian long
    """
    return struct.unpack("!L", socket.inet_aton(s))[0]


def long2ip(l):
    """Convert big-endian long representation of IP address to string
    """
    return socket.inet_ntoa(struct.pack("!L", l))

def mask2long(mask):
    """Convert numeric CIDR network mask to negative integer representation for bitwise operations.
    """
    assert mask >= 0 and mask <= 32
    return -(1 << (32 - mask)) 

def in_iplist(ip, l):
    """Check if IP address is in the list.
    List l may contain individual IP addresses or CIDR ranges.
    """
    # no input validations here as it should be fast
    for item in l:
        if '/' in item:
            a, mask = item.split('/')
            m = mask2long(mask)
            # IP range contains IP address when masked range equals masked address
            if (ip2long(a) & m) == (ip2long(ip) & m):
                return True
        else:
            if item == ip:
                return True
    return False




def process_commands(cmd_queue, whitelist):
    def is_ip_ignored(ip, whitelist, rcmd):
        """Prevent adding DROP rules and prevent deleting ACCEPT rules for whitelisted IPs.
        Also log the such attempts as warnings.
        """
        if in_iplist(ip, whitelist):
            if (modify == 'I' and action == 'DROP') or (modify == 'D' and action == 'ACCEPT'):
                log.warn("Request {} related to whitelisted IP address {} ignored.".format(str(rcmd), ip))
                return True
        return False
 
    while True:
        rcmd = cmd_queue.get()
        lcmd = cmdexe.construct_iptables(rcmd)
        #TODO check for duplicates, check the whitelist, execute command
        #TODO for whitelist addresses action/noaction depends on chain.input.action:
        
        modify = rcmd['modify']
        action = rcmd['action']
        chain = rcmd['chain']

        ip1 = rcmd['ip1']
        if is_ip_ignored(ip1, whitelist, rcmd): 
            continue
        
        if chain == 'forward':
            ip2 = rcmd.get('ip2')
            if is_ip_ignored(ip2, whitelist, rcmd):
                continue

        #TODO need to think over the in memory representation of 
        print "Got from Queue:\n{}\n{}".format(rcmd, lcmd)
        cmd_queue.task_done()



def main():
    configfile = parse_commandline()
    rfwconf = config.RfwConfig(configfile)


    cmd_queue = Queue()
    consumer = Thread(target=process_commands, args=(cmd_queue, rfwconf.whitelist()))
    consumer.setDaemon(True)
    consumer.start()

    #passing HandlerClass to SSLServer is very limiting, seems like a bad design of BaseServer. In order to pass extra info to RequestHandler without using global variable we have to wrap the class in closure
    HandlerClass = create_requesthandler(rfwconf, cmd_queue)
    if rfwconf.is_outward_server():
        server_address = (rfwconf.outward_server_ip(), int(rfwconf.outward_server_port()))
        httpd = SSLServer(
                    server_address, 
                    HandlerClass, 
                    rfwconf.outward_server_certfile(), 
                    rfwconf.outward_server_keyfile())
        sa = httpd.socket.getsockname()
        print "Serving HTTPS on", sa[0], "port", sa[1], "..."
        httpd.serve_forever()

    

    assert False, "There should be at least one non-daemon"

if __name__ == "__main__":
    main()
