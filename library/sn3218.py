import curses
import sys
import threading
import time
from time import sleep

try:
    from smbus import SMBus
except ImportError:
    if sys.version_info[0] < 3:
        raise ImportError("This library requires python-smbus\nInstall with: sudo apt install python-smbus")
    elif sys.version_info[0] == 3:
        raise ImportError("This library requires python3-smbus\nInstall with: sudo apt install python3-smbus")

__version__ = '1.2.7'

# technically the next line is hex(0b1010100) although the SN3218 datasheet
# says 1010 1000, because the slave address is 7 bits followed by a r/w bit
I2C_ADDRESS = 0x54
CMD_ENABLE_OUTPUT = 0x00
CMD_SET_PWM_VALUES = 0x01
CMD_ENABLE_LEDS = 0x13
CMD_UPDATE = 0x16
CMD_RESET = 0x17


def i2c_bus_id():
    """
    Returns the i2c bus ID.
    """
    with open('/proc/cpuinfo') as cpuinfo:
        revision = [l[12:-1] for l in cpuinfo if l[:8] == "Revision"][0]
    # https://www.raspberrypi.org/documentation/hardware/raspberrypi/revision-codes/README.md
    return 1 if int(revision, 16) >= 4 else 0


def i2c_write(*arg, update=False):
    """
    Helper function to avoid repetition.
    """
    i2c.write_i2c_block_data(I2C_ADDRESS, *arg)
    if update:
        i2c.write_i2c_block_data(I2C_ADDRESS, CMD_UPDATE, [0xFF])

def enable():
    """ 
    Enables output.
    """
    i2c_write(CMD_ENABLE_OUTPUT, [0x01])


def disable():
    """ 
    Disables output.

    SN3218 calls this mode "Software shutdown", and suggests using it to
    flash the LEDs, or as a power-saving mode when not in use.
    """
    i2c_write(CMD_ENABLE_OUTPUT, [0x00])


def reset():
    """ 
    Resets all internal registers to default.
    """
    i2c_write(CMD_RESET, [0xFF])


def enable_leds(enable_mask):
    """ 
    Enables or disables each LED channel. The first 18 bit values are
    used to determine the state of each channel (1=on, 0=off); if fewer
    than 18 bits are provided the remaining channels are turned off.

    Args:
        enable_mask (int): up to 18 bits of data
    Raises:
        TypeError: if enable_mask is not an integer.
    """
    # enable_mask 
    # e.g. 0b100000_000001_000001 enables LEDs 18, 7, 1 (red)
    # however on the way to the SN3218, register 0x13 controls channels 1-6
    # the datasheet makes clear that 0b00_000001 enables OUT1.
    # This functions ultimately flips the direction of the provided enable_mask
    # to send OUT1-OUT6 values to register 0x13 first.

    if not isinstance(enable_mask, int):
        raise TypeError("enable_mask must be an integer")

    bitmask = 0b111111
    i2c_write(CMD_ENABLE_LEDS, 
             [enable_mask & bitmask,
             (enable_mask >> 6) & bitmask,
             (enable_mask >> 12) & bitmask],
             update=True)


def channel_gamma(channel, gamma_table):
    """ 
    Overrides the gamma table for a single channel.

    Args:
        channel (int): channel number
        gamma_table (list): list of 256 gamma correction values
    Raises:
        TypeError: if channel is not an integer.
        ValueError: if channel is not in the range 0..17.
        TypeError: if gamma_table is not a list.
    """	
    global channel_gamma_table

    if not isinstance(channel, int):
        raise TypeError("channel must be an integer")

    if not 0 <= channel <= 17:
        raise ValueError("channel be an integer in the range 0..17")

    if not isinstance(gamma_table, list) or len(gamma_table) != 256:
        raise TypeError("gamma_table must be a list of 256 integers")

    channel_gamma_table[channel] = gamma_table	


def output(values):
    """ 
    Outputs a new set of values to the driver

    Args:
        values (list): channel number
    Raises:
        TypeError: if values is not a list.
    """ 
    if not isinstance(values, list) or len(values) != 18:
        raise TypeError("values must be a list of 18 integers")

    i2c_write(CMD_SET_PWM_VALUES,
            [channel_gamma_table[i][values[i]] for i in range(18)],
            update=True)


def output_raw(values):
    """
    Like output(), but bypasses channel_gamma_table
    """
    if not isinstance(values, list) or len(values) != 18:
        raise TypeError("values must be a list of 18 integers")

    i2c_write(CMD_SET_PWM_VALUES, values, update=True)

DELAY = 0.01

def fader():
    b = 0
    enable()
    # quick-n-dirty colour selector
    enable_leds(0b100000_000001_000001) # red
    enable_leds(0b010000_000010_000010) # orange
    enable_leds(0b001000_000100_000100) # yellow
    enable_leds(0b000010_000000_101000) # green
    enable_leds(0b000100_100000_010000) # blue
    enable_leds(0b000001_011000_000000) # white
    enable_leds(0b000101_000000_001111) # arm
    enable_leds(0b111111_111111_111111) # all

    while True:
        while True:
            output([b] * 18)
            if DELAY > 0:
                sleep(DELAY)
            if b == 255:
                break
            else:
                b += 1
        if DELAY > 0:
            # "blip" at mid-point for visibility
            disable()
            sleep(DELAY)
            enable()
        while True:
            output([b] * 18)
            if DELAY > 0:
                sleep(DELAY)
            if b == 0:
                break
            else:
                b -= 1

def recalc_GAMMA():
    global default_gamma_table, channel_gamma_table

    default_gamma_table = [int(255 * (i / 255.0) ** GAMMA) for i in range(256)]
    channel_gamma_table = [default_gamma_table] * 18
    

def arrows(window):
    global DELAY, GAMMA

    while True:
        window.clear()
        window.addstr(f"DELAY: {DELAY:.3f}\nGAMMA: {GAMMA:.2f}\n")
        window.addstr("Use cursor keys to change\nAny other key to quit\n")
        window.refresh()
        key = window.getkey()
        if key == 'KEY_RIGHT':
            DELAY += 0.001
        elif key == 'KEY_LEFT':
            DELAY -= 0.001
        elif key == 'KEY_UP':
            GAMMA += 0.1
            recalc_GAMMA()
        elif key == 'KEY_DOWN':
            GAMMA -= 0.1
            recalc_GAMMA()
        else:
            # quit
            break


def calibrate_gamma(window):

    # first thread: fader
    f = threading.Thread(target=fader)
    f.daemon = True
    f.start()

    # second thread: keyboard
    k = threading.Thread(target=arrows, args=(window,))
    k.start()

    # wait for keyboard quit
    k.join()
    reset()
    disable()


i2c = SMBus(i2c_bus_id())

SN3218_TABLE7_GAMMA = [0, 1, 2, 4, 6, 10, 13, 18,
        22, 28, 33, 39, 46, 53, 61, 69,
        78, 86, 96, 106, 116, 126, 138, 149,
        161,173,186,199, 212, 226, 240, 255]

# generate a good default gamma table
old_default_gamma_table = [int(pow(255, float(i - 1) / 255)) for i in range(256)]
#default_gamma_table = [int(255 ** ( (i-1) / 255.0) for i in range(256)]

GAMMA=1.4
default_gamma_table = [int(255 * (i / 255) ** GAMMA) for i in range(256)]

channel_gamma_table = [default_gamma_table] * 18

enable_leds(0b111111111111111111)

if __name__ == "__main__":


    if '--calibrate' in sys.argv:
        curses.wrapper(calibrate_gamma)
        sys.exit()

    print("sn3218 test cycles")
    
    import time
    import math

    # enable output
    enable()
    enable_leds(0b111111111111111111)
    
    print(">> test enable mask (on/off)")
    enable_mask = 0b000000000000000000
    output([0x10] * 18)
    for i in range(10):
        enable_mask = ~enable_mask
        enable_leds(enable_mask)
        time.sleep(0.15)

    print(">> test enable mask (odd/even)")
    enable_mask = 0b101010101010101010
    output([0x10] * 18)
    for i in range(10):
        enable_mask = ~enable_mask
        enable_leds(enable_mask)
        time.sleep(0.15)

    print(">> test enable mask (rotate)")
    enable_mask = 0b100000100000100000
    output([0x10] * 18)
    for i in range(10):
        enable_mask = ((enable_mask & 0x01) << 18) | enable_mask >> 1
        enable_leds(enable_mask)
        time.sleep(0.15)

    print(">> test gamma gradient")
    enable_mask = 0b111111111111111111
    enable_leds(enable_mask)
    for i in range(256):
        output([((j * (256//18)) + (i * (256//18))) % 256 for j in range(18)])
        time.sleep(0.01)

    print(">> test gamma fade")
    enable_mask = 0b111111111111111111
    enable_leds(enable_mask)
    for i in range(512):
        output([int((math.sin(float(i)/64.0) + 1.0) * 128.0)]*18)
        time.sleep(0.01)


    print(">> test linear fade")
    for g in (1.0, 1.2, 1.4, 1.6, 6.8):
        GAMMA = g
        print(">>> gamma = " + str(GAMMA))
        default_gamma_table = [int(255 * (i / 255) ** GAMMA) for i in range(256)]

        channel_gamma_table = [default_gamma_table] * 18

        for i in range(256):
            output([i] * 18)
            time.sleep(0.01)
        disable()
        time.sleep(0.1)
        enable()
        for i in range(255,0,-1):
            output([i]*18)
            time.sleep(0.01)

    # turn everything off and disable output
    output([0] * 18)
    disable()
