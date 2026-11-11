"""Just enough CMSIS-DAP v2 for the bench's tools: find a probe, send commands, time repeated runs,
and bring an MSPM0 up halted.

Copied once from mculink-verge's tools/dap.py and tools/coverage.py on 2026-10-20, and owned
here since. It speaks standard CMSIS-DAP; the one vendor command it names turns our firmware's cJTAG off.
"""

import dataclasses
import sys
import time

import usb.core
import usb.util

# Stock MCU-Link calls its bulk interface "MCU-LINK (r0FB) CMSIS-DAP V3.172" and ours calls it
# "CMSIS-DAP v2", so match on the part of the name the spec actually stipulates.
CMSIS_DAP = "cmsis-dap"

DAP_INFO = 0x00
DAP_CONNECT = 0x02
DAP_TRANSFER = 0x05
DAP_TRANSFER_BLOCK = 0x06
DAP_SWJ_CLOCK = 0x11
DAP_SWJ_SEQUENCE = 0x12
DAP_VENDOR_CJTAG_CONTROL = 0x83  # ours before identify format 7

CSW = 0x2300_0052
DHCSR = 0xE000_EDF0
MSPM0_DPIDR = 0x6BA0_2477
# Deep sleep switches off PD1, and an MSPM0's AHB-AP with it. TI's debug setting for PWR-AP (AP 4)
# DPREC0 keeps the part out of it: INHIBITSLEEP, DEBUGPOWER, halt on reset and FORCEACTIVE.
MSPM0_PWR_AP_SELECT = 0x0400_0000
MSPM0_DPREC0_DEBUG = 0x0019_0008

INFO_FIELDS = {
    0x01: ("vendor", "str"),
    0x02: ("product", "str"),
    0x03: ("serial", "str"),
    0x04: ("protocol version", "str"),
    0x05: ("target device vendor", "str"),
    0x06: ("target device name", "str"),
    0x07: ("target board vendor", "str"),
    0x08: ("target board name", "str"),
    0x09: ("firmware version", "str"),
    0xF0: ("capabilities", "bytes"),
    0xF1: ("test domain timer", "word"),
    0xFB: ("UART rx buffer", "word"),
    0xFC: ("UART tx buffer", "word"),
    0xFD: ("SWO trace buffer", "word"),
    0xFE: ("max packet count", "byte"),
    0xFF: ("max packet size", "short"),
}


class Probe:
    """One CMSIS-DAP v2 probe, held open on its bulk pair."""

    def __init__(self, device, interface, out_ep, in_ep, serial, product, vendor):
        self.device = device
        self.interface = interface
        self.out_ep = out_ep
        self.in_ep = in_ep
        self.serial = serial
        self.product = product
        self.vendor = vendor

    def drain(self) -> int:
        """Throw away replies queued by a run that stopped early.

        Nothing in the protocol resynchronises a bulk endpoint, so a reply nobody read is handed
        to whatever asks next and every answer after it is off by one. Pipelined runs make this
        routine rather than rare.
        """
        count = 0
        while True:
            try:
                self.in_ep.read(self.in_ep.wMaxPacketSize, 50)
                count += 1
            except usb.core.USBError:
                return count

    def command(self, request: bytes, timeout: int = 2000) -> bytes:
        self.out_ep.write(request, timeout)
        reply = bytes(self.in_ep.read(self.in_ep.wMaxPacketSize, timeout))
        if not reply or reply[0] != request[0]:
            raise RuntimeError(
                f"asked for 0x{request[0]:02x}, got 0x{reply[0]:02x} -- the probe is out of step"
            )
        return reply

    def info(self, field: int) -> bytes:
        reply = self.command(bytes([DAP_INFO, field]))
        return reply[2 : 2 + reply[1]]

    def connect(self, port: int = 1) -> int:
        return self.command(bytes([DAP_CONNECT, port]))[1]

    def swj_clock(self, hz: int) -> int:
        return self.command(bytes([DAP_SWJ_CLOCK]) + hz.to_bytes(4, "little"))[1]

    def swj_sequence(self, bits: int, data: bytes) -> int:
        # A count of 256 is encoded as 0; anything above needs several commands.
        count = 0 if bits == 256 else bits
        return self.command(bytes([DAP_SWJ_SEQUENCE, count]) + data)[1]

    def release(self) -> None:
        usb.util.dispose_resources(self.device)


def find(serial: str | None = None, vid: int = 0x1FC9, pid: int = 0x0143) -> list[Probe]:
    """Every attached probe that offers a CMSIS-DAP v2 bulk interface."""
    probes = []
    for device in usb.core.find(find_all=True, idVendor=vid, idProduct=pid):
        try:
            device_serial = usb.util.get_string(device, device.iSerialNumber) or ""
            product = usb.util.get_string(device, device.iProduct) or ""
            manufacturer = usb.util.get_string(device, device.iManufacturer) or ""
        except (usb.core.USBError, ValueError):
            # Some firmware builds omit string descriptors. The vendor-class bulk interface
            # still identifies the CMSIS-DAP transport well enough to use its commands.
            device_serial = product = manufacturer = ""
        if serial is not None and device_serial != serial:
            continue
        for configuration in device:
            for interface in configuration:
                name = ""
                if interface.iInterface:
                    try:
                        name = usb.util.get_string(device, interface.iInterface) or ""
                    except (usb.core.USBError, ValueError):
                        name = ""
                if interface.bInterfaceClass != 0xFF or (name and CMSIS_DAP not in name.lower()):
                    continue
                out_ep = in_ep = None
                for endpoint in interface:
                    if usb.util.endpoint_type(endpoint.bmAttributes) != usb.util.ENDPOINT_TYPE_BULK:
                        continue
                    if usb.util.endpoint_direction(endpoint.bEndpointAddress) == usb.util.ENDPOINT_OUT:
                        out_ep = out_ep or endpoint
                    else:
                        in_ep = in_ep or endpoint
                if out_ep is None or in_ep is None:
                    continue
                if device.is_kernel_driver_active(interface.bInterfaceNumber):
                    device.detach_kernel_driver(interface.bInterfaceNumber)
                probes.append(
                    Probe(device, interface, out_ep, in_ep, device_serial, product, manufacturer)
                )
                break
    return probes


def swd_reset(probe: "Probe") -> int:
    """Put the target's debug port into SWD and leave it addressable.

    Line reset, the JTAG-to-SWD switch word, line reset again, then idle. The DPIDR read that
    follows is what actually clears the reset condition, and its value says a target answered.
    """
    probe.swj_sequence(56, b"\xff" * 7)
    probe.swj_sequence(16, b"\x9e\xe7")
    probe.swj_sequence(56, b"\xff" * 7)
    probe.swj_sequence(8, b"\x00")
    reply = probe.command(bytes([DAP_TRANSFER, 0, 1, 0x02]))
    if reply[1] != 1 or reply[2] != 1:
        raise RuntimeError(f"DPIDR read answered count {reply[1]} ack {reply[2]}")
    return int.from_bytes(reply[3:7], "little")


def le(v: int) -> bytes:
    return v.to_bytes(4, "little")


def wait_powered(probe: Probe, index: int) -> None:
    for _ in range(100):
        reply = probe.command(bytes([DAP_TRANSFER, index, 1, 0x06]))
        if int.from_bytes(reply[3:7], "little") & 0xA000_0000 == 0xA000_0000:
            return
    raise RuntimeError("the DAP did not power up")


def power_up_and_halt(probe: Probe, index: int) -> None:
    wait_powered(probe, index)
    steps = bytes([0x08]) + le(0) + bytes([0x01]) + le(CSW)
    steps += bytes([0x05]) + le(DHCSR) + bytes([0x0D]) + le(0xA05F_0003) + bytes([0x05]) + le(DHCSR) + bytes([0x0F])
    reply = probe.command(bytes([DAP_TRANSFER, index, 6]) + steps)
    dhcsr = int.from_bytes(reply[3:7], "little")
    if reply[1] != 6 or reply[2] != 1 or not dhcsr & (1 << 17):
        raise RuntimeError(f"halt: count {reply[1]} ack {reply[2]} DHCSR 0x{dhcsr:08x}")


@dataclasses.dataclass
class Run:
    """What one timed run of a repeated command came to."""

    done: int  # commands answered with every transfer OK
    words: int  # the transfers those carried
    seconds: float
    cpu: float  # host CPU time over the run
    error: str | None  # why the run stopped early, if it did


def transfer_counts(payload: bytes, reply: bytes) -> tuple[int, int]:
    """The transfer count and last acknowledge of a DAP_Transfer or DAP_TransferBlock reply."""
    if payload[0] == DAP_TRANSFER:
        return reply[1], reply[2]
    return int.from_bytes(reply[1:3], "little"), reply[3]


def pipelined_blocks(probe: Probe, payload: bytes, blocks: int, inflight: int, words: int) -> Run:
    """Run `blocks` copies of one command with `inflight` of them sent ahead of their replies.

    CMSIS-DAP v2 allows that -- DAP_Info reports how many packets the probe will hold -- and it is
    the one thing that separates a gap the firmware owns from a gap the host owns. A run that stops
    early leaves replies queued, which the next run would read as its own: drain before going on.
    """
    started = time.perf_counter()
    cpu = time.process_time()
    sent = done = moved = 0
    error = None
    while done < blocks:
        while sent < blocks and sent - done < inflight:
            probe.out_ep.write(payload, 2000)
            sent += 1
        reply = bytes(probe.in_ep.read(probe.in_ep.wMaxPacketSize, 2000))
        count, ack = transfer_counts(payload, reply)
        if reply[0] != payload[0] or count != words or ack != 1:
            error = f"command 0x{payload[0]:02x} returned {count} words, ack {ack}"
            break
        done += 1
        moved += count
    return Run(done, moved, time.perf_counter() - started, time.process_time() - cpu, error)


def queued_blocks(probe: Probe, payload: bytes, blocks: int, depth: int, words: int) -> Run:
    """Run `blocks` copies of one command with `depth` of them queued in the host controller.

    The synchronous loop in `pipelined_blocks` cannot go deeper than the probe's request slots: a
    write the probe refuses blocks the only thread, which then never reads the reply that would free
    a slot. libusb's asynchronous API queues the writes and keeps `depth` reads posted, so the probe
    takes the next command the moment a slot frees and never waits on the host. That makes this the
    ceiling any host could reach through the current USB path. It releases `probe`; find the probe
    again to go on.
    """
    import usb1

    serial = probe.serial
    usb_id = (probe.device.idVendor, probe.device.idProduct)
    number = probe.interface.bInterfaceNumber
    out_address = probe.out_ep.bEndpointAddress
    in_address = probe.in_ep.bEndpointAddress
    size = probe.in_ep.wMaxPacketSize
    probe.release()

    with usb1.USBContext() as context:
        handle = None
        for device in context.getDeviceIterator(skip_on_error=True):
            if (device.getVendorID(), device.getProductID()) != usb_id:
                continue
            candidate = device.open()
            if candidate.getSerialNumber() == serial:
                handle = candidate
                break
            candidate.close()
        if handle is None:
            sys.exit(f"{serial} vanished between setup and the queued run")
        with handle.claimInterface(number):
            state = {"sent": 0, "posted": 0, "done": 0, "words": 0, "owed": 0, "error": None}
            free_out = [handle.getTransfer() for _ in range(depth)]
            reads = [handle.getTransfer() for _ in range(depth)]

            def send() -> None:
                if state["sent"] >= blocks:
                    return
                if not free_out:
                    state["owed"] += 1
                    return
                transfer = free_out.pop()
                transfer.setBulk(out_address, payload, callback=on_out, timeout=5000)
                transfer.submit()
                state["sent"] += 1

            def on_out(transfer) -> None:
                free_out.append(transfer)
                if transfer.getStatus() != usb1.TRANSFER_COMPLETED:
                    state["error"] = state["error"] or f"write status {transfer.getStatus()}"
                    return
                if state["owed"]:
                    state["owed"] -= 1
                    send()

            def post(transfer) -> None:
                transfer.setBulk(in_address, size, callback=on_in, timeout=5000)
                transfer.submit()
                state["posted"] += 1

            def on_in(transfer) -> None:
                if transfer.getStatus() != usb1.TRANSFER_COMPLETED:
                    state["error"] = state["error"] or f"read status {transfer.getStatus()}"
                    return
                reply = bytes(transfer.getBuffer()[: transfer.getActualLength()])
                count, ack = transfer_counts(payload, reply)
                if reply[0] != payload[0] or count != words or ack != 1:
                    state["error"] = state["error"] or f"reply {reply[:4].hex()} to block {state['done']}"
                    return
                state["done"] += 1
                state["words"] += count
                if state["posted"] < blocks:
                    post(transfer)
                send()

            started = time.perf_counter()
            cpu = time.process_time()
            for transfer in reads[: min(depth, blocks)]:
                post(transfer)
            for _ in range(min(depth, blocks)):
                send()
            while state["done"] < blocks and state["error"] is None:
                context.handleEventsTimeout(1.0)
            elapsed = time.perf_counter() - started
            cpu = time.process_time() - cpu
            if state["error"] is not None:
                for transfer in reads + [t for t in free_out]:
                    try:
                        transfer.cancel()
                    except usb1.USBError:
                        pass
        handle.close()
    return Run(state["done"], state["words"], elapsed, cpu, state["error"])
