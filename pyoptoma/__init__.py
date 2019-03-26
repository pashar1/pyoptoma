import logging
import serial
import threading
import async_timeout
import time

OPTOMA_COMMANDS = {
    "TURN_ON":'~0000 1\r',
    "TURN_OFF":'~0000 0\r',
    "PWR":'~00124 1\r',
    "SOURCE":'~00121 1\r',
    "DISPLAY_MODE":'~00123 1\r',
    "HDMI1":'~0012 1\r',
    "HDMI2":'~0012 15\r',
    "VGA":'~0012 8\r',
    "COMPONENT":'~0012 14\r',
    "VIDEO":'~0012 10\r',
    "3D_OFF":'~00405 0\r',
    "3D_SBS":'~00405 1\r',
    "3D_TTB":'~00405 3\r',
    "3D_SEQ":'~00405 4\r'
}  
SOURCE_LIST = {
    'HDMI1': 'HDMI1',
    'HDMI2': 'HDMI2',
    'VGA': 'VGA',
    'COMPONENT': 'COMPONENT',
    'VIDEO': 'VIDEO'
}

TIMEOUT_TIMES = {
    'TURN_ON': 40,
    'TURN_OFF': 60,
    'SOURCE': 2,
    'ALL': 2
}
SOURCE_MAP = {
    'OK00': None,
    'OK02': 'VGA',
    'OK05': 'VIDEO',
    'OK07': 'HDMI1',
    'OK08': 'HDMI2',
    'OK11': 'COMPONENT'
}

TURN_ON = "TURN_ON"
TURN_OFF = "TURN_OFF"
POWER = "PWR"
SOURCE = "SOURCE"
BUSY = "BUSY"

_LOGGER = logging.getLogger(__name__)

class OptomaThread(threading.Thread):

   def __init__(self, serial, notify_event):
      threading.Thread.__init__(self, name='OptomaThread', daemon=True)
      self._serial = serial
      self._lastline = None
      self._recv_event = threading.Event()
      self._notify_event = notify_event

   def run(self):
      while True:
         line = self._readline()
         if len(line)==5 and line[0]=='I':
            self._notify_event(line)
            continue
         self._lastline = line
         self._recv_event.set()

   def _readline(self):
      output = ''
      while True:
         byte = self._serial.read(size=1)
         if (byte[0] == 0x0d):
            break

         """working around bug when projector sends 0x00 
         send randomly in the front of the some replies"""
         if (byte[0] != 0x00):
            output += byte.decode('utf-8')

         """working with bug in projector when INFO1 is not sending 0x0D"""
         if output == 'INFO1':
            break
      _LOGGER.debug("got something from serial port %s", output)
      return output

   def get_response(self):
      self._recv_event.wait()
      self._recv_event.clear()
      return self._lastline

class Projector:

   def __init__(self, url):
      self._serial = serial.serial_for_url(url, baudrate=9600, parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE)
      self._events = {}
      self._thread = OptomaThread(self._serial, self._notify_event)
      self._thread.start()
      self._command_lock = threading.Lock()
      self.__initLock()

   def _send(self, command):
      with self._command_lock:
         _LOGGER.info('Send "%s"', command)
         self._serial.write(command.encode('utf-8'))
         return 'P'

   def _sendrecv(self, command):
      with self._command_lock:
         _LOGGER.info('Send "%s"', command)
         self._serial.write(command.encode('utf-8'))
         result = self._thread.get_response()
         _LOGGER.info('Recv "%s"', result)
         return result

   def _add_event(self, event_name, handler):
      event_list = self._events.get(event_name, None)
      if event_list == None:
         event_list = []
         self._events[event_name] = event_list
      event_list.append(handler)

   def _notify_event(self, event_name):
      _LOGGER.info('Event "%s"', event_name)
      line = str(event_name)
      if line == 'INFO0':  
         _LOGGER.info('Event name is: %s', event_name)
         _LOGGER.info('Projector powered off')
      event_list = self._events.get(event_name, None)
      _LOGGER.info('Event list %s', event_list)
      _LOGGER.info('Line is %s',line)
      if event_list is not None:
         _LOGGER.info('Getting handler')
         for handler in event_list:
            _LOGGER.info('handler %s', handler)
            handler()

   def __initLock(self):
      """Init lock for sending request to projector when it is busy."""
      self._isLocked = False
      self._timer = 0
      self._operation = False

   def __setLock(self, command):
      """Set lock on requests."""
      if command in (TURN_ON, TURN_OFF):
          self._operation = command
      elif command in SOURCE_LIST:
          self._operation = SOURCE
      else:
          self._operation = ALL
      self._isLocked = True
      self._timer = time.time()

   def __unLock(self):
      """Unlock sending requests to projector."""
      self._operation = False
      self._timer = 0
      self._isLocked = False


   def __checkLock(self):
      """
      Lock checking.

      Check if there is lock pending and check if enough time
      passed so requests can be unlocked.
      """
      if self._isLocked:
          if (time.time() - self._timer) > TIMEOUT_TIMES[self._operation]:
             self.__unLock()
             return False
          return True
      return False


   async def get_property(self, command):
      """Get property state from device."""
      _LOGGER.debug("Getting property %s", command)
      if self.__checkLock():
          return BUSY
      timeout = self.__get_timeout(command)
      response = self._sendrecv(OPTOMA_COMMANDS.get(command))
      if not response:
          return False
      try:
          if command == 'PWR':
             if len(response)==3: 
                if response[2]=='1':
                   return 'on'
                if response[2]=='0':
                   return 'off'
          if command == 'SOURCE':
             if len(response)==4:
                _LOGGER.info("Got source from the projector %s", SOURCE_MAP.get(response))
                return SOURCE_MAP.get(response)
      except KeyError:
          return BUSY

   def powered_off(self, handler):
       self._add_event('INFO0', handler)

   def powering_on(self, handler):
       self._add_event('INFO1', handler)

   def powering_off(self, handler):
       self._add_event('INFO2', handler)

   def send_command(self, command):
       if self.__checkLock():
          return False
       self.__setLock(command)
       result = self._sendrecv(OPTOMA_COMMANDS.get(command))
       _LOGGER.debug("Send command %s to projector", command)
       _LOGGER.debug("Got back from the send/recv: %s", result)
       if result == 'P':
         _LOGGER.debug("command %s completed successfuly", command)
         return 'OK' 
       if result == 'F': 
         _LOGGER.debug("command %s failed, projector probably busy", command)
         return 'BUSY'

   def __get_timeout(self, command):
       if command in TIMEOUT_TIMES:
         return TIMEOUT_TIMES[command]
       else:
         return TIMEOUT_TIMES['ALL']

