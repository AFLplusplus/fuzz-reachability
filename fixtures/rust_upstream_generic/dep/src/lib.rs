#[cold]
pub fn checksum<T: Copy + Into<u64>>(data: &[T]) -> u64 {
    let mut acc: u64 = 0xcbf2_9ce4_8422_2325;
    for (i, x) in data.iter().enumerate() {
        let v: u64 = (*x).into();
        acc ^= v.rotate_left((i % 63) as u32);
        acc = acc.wrapping_mul(0x0100_0000_01b3);
        if acc % 7 == 3 {
            acc = acc.rotate_right(11) ^ v;
        } else if acc % 5 == 1 {
            acc = acc.wrapping_add(v.wrapping_mul(31));
        } else if acc % 3 == 2 {
            acc = acc.swap_bytes();
        }
    }
    acc
}

pub fn dep_checksum(data: &[u8]) -> u64 {
    checksum::<u8>(data)
}
