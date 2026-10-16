"""Demo network device: SSH password login checked against RADIUS (PAP).

Authenticated users get a small simulated device CLI, never a real shell.
Port forwarding, agent/X11 forwarding and SFTP are not offered.
Settings come from the environment: RADIUS_HOST, RADIUS_PORT, RADIUS_SECRET,
DEVICE_HOSTNAME, SSH_PORT and HOST_KEY (path to the persistent host key).
"""
import asyncio
import logging
import os
import time

import asyncssh

from radius_client import ACCESS_ACCEPT, access_request

HOSTNAME = os.environ.get('DEVICE_HOSTNAME', 'edge-sw01')
RADIUS_HOST = os.environ['RADIUS_HOST']
RADIUS_PORT = int(os.environ.get('RADIUS_PORT', '1812'))
RADIUS_SECRET = os.environ['RADIUS_SECRET']
MAX_FAILURES = 3
log = logging.getLogger('ssh-demo')


class Device(asyncssh.SSHServer):
    def connection_made(self, conn):
        self.conn = conn
        self.peer = conn.get_extra_info('peername')[0]
        self.failures = 0

    def begin_auth(self, username):
        self.conn.send_auth_banner(
            f'{HOSTNAME}: authorized access only.\n'
            'Log in with your Authentik username or email and password.\n')
        return True

    def password_auth_supported(self):
        return True

    async def validate_password(self, username, password):
        code = None
        # RADIUS limits: User-Name fits one attribute, PAP passwords <= 128 bytes.
        if 0 < len(username.encode()) <= 253 and len(password.encode()) <= 128:
            try:
                code, _ = await asyncio.get_running_loop().run_in_executor(
                    None, access_request, RADIUS_HOST, RADIUS_PORT, RADIUS_SECRET,
                    username, password, HOSTNAME, 10)
            except (OSError, AssertionError) as e:
                log.warning('RADIUS %s:%d error: %s', RADIUS_HOST, RADIUS_PORT, e)
        accepted = code == ACCESS_ACCEPT
        log.info('login user=%r peer=%s result=%s', username, self.peer,
                 'Access-Accept' if accepted else 'reject')
        if not accepted:
            self.failures += 1
            await asyncio.sleep(2)
            if self.failures >= MAX_FAILURES:
                self.conn.disconnect(asyncssh.DISC_NO_MORE_AUTH_METHODS_AVAILABLE,
                                     'Too many authentication failures')
        return accepted


def run_command(command, username, login):
    if command in ('show users', 'sh users'):
        return ('    Line       User       Host(s)              Idle       Location\n'
                f'*  2 vty 0     {username:<10} idle                 00:00:00   {login}\n')
    if command in ('show aaa servers', 'sh aaa servers'):
        return (f'RADIUS: id 1, priority 1, host {RADIUS_HOST}, auth-port {RADIUS_PORT}\n'
                '     State: current UP, last result Access-Accept\n')
    if command == '?':
        return ('  exit              Exit from the EXEC\n'
                '  show aaa servers  Show AAA server state\n'
                '  show users        Display information about terminal lines\n')
    if command:
        return "% Invalid input detected at '^' marker.\n"
    return ''


async def cli(process):
    username = process.get_extra_info('username')
    login = time.strftime('%H:%M:%S %Z %a %b %d %Y')
    if process.command is not None:
        process.stdout.write(run_command(process.command.strip(), username, login))
        process.exit(0)
        return
    process.stdout.write(f'\n% Authenticated by RADIUS server {RADIUS_HOST}:{RADIUS_PORT}'
                         f' (Access-Accept)\n\n')
    while True:
        process.stdout.write(f'{HOSTNAME}>')
        try:
            line = await process.stdin.readline()
        except asyncssh.BreakReceived:
            continue
        except asyncssh.TerminalSizeChanged:
            continue
        command = line.strip()
        if not line or command in ('exit', 'logout', 'quit'):
            break
        process.stdout.write(run_command(command, username, login))
    process.exit(0)


async def main():
    await asyncssh.create_server(
        Device, '', int(os.environ.get('SSH_PORT', '2222')),
        server_host_keys=[os.environ['HOST_KEY']], process_factory=cli,
        login_timeout=60, keepalive_interval=30,
        agent_forwarding=False, x11_forwarding=False, sftp_factory=None)
    log.info('listening as %s, RADIUS %s:%d', HOSTNAME, RADIUS_HOST, RADIUS_PORT)
    await asyncio.Event().wait()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    asyncio.run(main())
