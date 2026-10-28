//! The addressing of a 24C32-style EEPROM: a two-byte word address, page writes that wrap within
//! their page, and reads that continue from wherever the last access ended.
#![no_std]

/// Bytes held: the 24C32's 4 KiB cut to fit the target's 4 KiB of SRAM. Address bits from bit 10
/// up are ignored, so the array repeats every 1 KiB of the address space.
pub const SIZE: usize = 1024;

/// A write's data wraps within its aligned page of this many bytes, as the 24C32's does.
pub const PAGE: usize = 32;

pub struct Eeprom {
    mem: [u8; SIZE],
    /// Where a read without an address starts.
    pointer: usize,
}

impl Eeprom {
    /// Byte `a` holds `a ^ (a >> 8)`, so data read from the wrong 256-byte block differs from the
    /// right one even before anything is written.
    pub const fn new() -> Self {
        let mut mem = [0; SIZE];
        let mut a = 0;
        while a < SIZE {
            mem[a] = (a ^ (a >> 8)) as u8;
            a += 1;
        }
        Self { mem, pointer: 0 }
    }

    /// Takes the word address from the first two bytes of a write. Returns false, with the pointer
    /// unchanged, when there are fewer than two.
    pub fn set_address(&mut self, bytes: &[u8]) -> bool {
        let [high, low, ..] = *bytes else {
            return false;
        };
        self.pointer = usize::from(u16::from_be_bytes([high, low])) % SIZE;
        true
    }

    /// Applies a write transaction that ended with a STOP: the word address, then any data.
    /// Returns true when it stored data, which is when a real part starts its write cycle.
    pub fn write(&mut self, bytes: &[u8]) -> bool {
        if !self.set_address(bytes) || bytes.len() == 2 {
            return false;
        }
        let data = &bytes[2..];
        let page = self.pointer - self.pointer % PAGE;
        let offset = self.pointer % PAGE;
        for (i, &byte) in data.iter().enumerate() {
            self.mem[page + (offset + i) % PAGE] = byte;
        }
        self.pointer = page + (offset + data.len()) % PAGE;
        true
    }

    /// The next bytes a read sends, at most `max` and never past the end of the array.
    pub fn read_run(&self, max: usize) -> &[u8] {
        &self.mem[self.pointer..SIZE.min(self.pointer + max)]
    }

    /// Moves the pointer past `n` bytes a controller has read, wrapping at the end of the array.
    pub fn advance(&mut self, n: usize) {
        self.pointer = (self.pointer + n) % SIZE;
    }
}

impl Default for Eeprom {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn read(eeprom: &mut Eeprom, n: usize) -> [u8; 64] {
        let mut out = [0; 64];
        let mut filled = 0;
        while filled < n {
            let run = eeprom.read_run(n - filled);
            out[filled..filled + run.len()].copy_from_slice(run);
            let len = run.len();
            eeprom.advance(len);
            filled += len;
        }
        out
    }

    #[test]
    fn fill_differs_between_blocks() {
        let mut eeprom = Eeprom::new();
        assert!(eeprom.set_address(&[0x00, 0x10]));
        let low = read(&mut eeprom, 1)[0];
        assert!(eeprom.set_address(&[0x01, 0x10]));
        let high = read(&mut eeprom, 1)[0];
        assert_eq!((low, high), (0x10, 0x11));
    }

    #[test]
    fn write_then_random_read() {
        let mut eeprom = Eeprom::new();
        assert!(eeprom.write(&[0x01, 0x23, 0xaa, 0xbb, 0xcc]));
        assert!(eeprom.set_address(&[0x01, 0x23]));
        assert_eq!(read(&mut eeprom, 3)[..3], [0xaa, 0xbb, 0xcc]);
    }

    #[test]
    fn page_write_wraps_within_its_page() {
        let mut eeprom = Eeprom::new();
        // Four bytes from 0x3e: two fit before the page boundary at 0x40, two wrap to 0x20.
        assert!(eeprom.write(&[0x00, 0x3e, 1, 2, 3, 4]));
        eeprom.set_address(&[0x00, 0x3e]);
        assert_eq!(read(&mut eeprom, 2)[..2], [1, 2]);
        eeprom.set_address(&[0x00, 0x20]);
        assert_eq!(read(&mut eeprom, 2)[..2], [3, 4]);
        assert_eq!(read(&mut eeprom, 1)[0], 0x22, "0x22 keeps its fill");
        eeprom.set_address(&[0x00, 0x40]);
        assert_eq!(read(&mut eeprom, 1)[0], 0x40, "the next page is untouched");
    }

    #[test]
    fn pointer_after_a_write_is_past_its_last_byte() {
        let mut eeprom = Eeprom::new();
        eeprom.write(&[0x00, 0x3e, 1, 2, 3]);
        assert_eq!(
            read(&mut eeprom, 1)[0],
            0x21,
            "0x20 was written, so the read starts at 0x21"
        );
    }

    #[test]
    fn data_longer_than_a_page_keeps_the_last_page_of_it() {
        let mut eeprom = Eeprom::new();
        let mut bytes = [0u8; 2 + PAGE + 1];
        for (i, b) in bytes[2..].iter_mut().enumerate() {
            *b = i as u8 + 1;
        }
        assert!(eeprom.write(&bytes));
        eeprom.set_address(&[0x00, 0x00]);
        let got = read(&mut eeprom, PAGE);
        assert_eq!(got[0], PAGE as u8 + 1, "the 33rd byte overwrote the first");
        assert_eq!(got[1], 2);
    }

    #[test]
    fn address_only_write_moves_the_pointer_and_stores_nothing() {
        let mut eeprom = Eeprom::new();
        assert!(!eeprom.write(&[0x02, 0x05]));
        assert_eq!(read(&mut eeprom, 1)[0], 0x07);
        assert!(!eeprom.write(&[0x02]), "one byte is not an address");
        assert_eq!(
            read(&mut eeprom, 1)[0],
            0x04,
            "the pointer kept moving from 0x206"
        );
    }

    #[test]
    fn addresses_alias_every_kib() {
        let mut eeprom = Eeprom::new();
        eeprom.write(&[0x04, 0x07, 0x5a]);
        eeprom.set_address(&[0x00, 0x07]);
        assert_eq!(read(&mut eeprom, 1)[0], 0x5a);
    }

    #[test]
    fn sequential_read_wraps_at_the_end_of_the_array() {
        let mut eeprom = Eeprom::new();
        eeprom.set_address(&[0x03, 0xfe]);
        assert_eq!(eeprom.read_run(8).len(), 2);
        assert_eq!(read(&mut eeprom, 4)[..4], [0xfd, 0xfc, 0x00, 0x01]);
    }
}
