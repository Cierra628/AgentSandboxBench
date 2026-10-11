from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from kernel_file_cache import GuestKernel


class KernelReaderTests(unittest.TestCase):
    def reader(self,va,large=None,levels=4):
        reader=GuestKernel.__new__(GuestKernel);reader.pgd=4096;reader.levels=levels
        entries={};table=4096
        for index,shift in enumerate((48,39,30,21,12) if levels==5 else (39,30,21,12)):
            if large==shift:entries[table+((va>>shift)&511)*8]=0x80000000|129;break
            target=4096*(index+2);entries[table+((va>>shift)&511)*8]=target|1;table=target
        reader.u64physical=lambda address:entries.get(address,0)
        return reader

    def test_four_level_page_walk(self):
        va=0xffff888000012345;reader=self.reader(va)
        self.assertEqual(reader.translate(va),20480+(va&4095))

    def test_five_level_page_walk(self):
        va=0xffff888000012345;reader=self.reader(va,levels=5)
        self.assertEqual(reader.translate(va),24576+(va&4095))

    def test_large_page_walk(self):
        va=0xffff888000012345;reader=self.reader(va,large=21)
        self.assertEqual(reader.translate(va),0x80000000+(va&((1<<21)-1)))

    def test_low_pointer_and_missing_entry_rejected(self):
        reader=self.reader(0xffff888000012345)
        with self.assertRaises(AssertionError):reader.translate(0x12345)
        reader.u64physical=lambda _:0
        with self.assertRaisesRegex(AssertionError,'nonpresent'):reader.translate(0xffff888000012345)

    def test_nonresident_metadata_does_not_fault(self):
        reader=GuestKernel.__new__(GuestKernel);reader.cache={};reader.slots=[]
        reader.pm=Mock();reader.pm.fileno.return_value=1;reader.mem=Mock();reader.mem.fileno.return_value=2
        with patch('kernel_file_cache.ram_address',return_value=4096),patch('kernel_file_cache.read_entry',return_value=0),patch('kernel_file_cache.os.pread',side_effect=AssertionError('must not fault')):
            with self.assertRaisesRegex(AssertionError,'refusing'):reader.physical(0,8)

    def test_kernel_configuration_uses_supported_get_api(self):
        import tempfile
        from kernel_file_cache import classify
        pause=Mock();pause.paused=True;pause.state.return_value='Paused'
        def api(method,target):
            self.assertEqual((method,target),('GET','/vm/config'))
            return {'boot-source':{'kernel_image_path':'/unsupported'}}
        pause.api=api
        with tempfile.TemporaryDirectory() as tmp,patch('kernel_file_cache.identity',return_value=Path('/unused')):
            with self.assertRaisesRegex(AssertionError,'unexpected private VMM kernel'):
                classify(Path(tmp),{}, {},pause)

    def test_compound_tail_is_explicitly_unsupported(self):
        reader=GuestKernel.__new__(GuestKernel);values={64:16,72:1}
        reader.u64=lambda address:values[address]
        with self.assertRaisesRegex(AssertionError,'compound'):reader.page_inode(1,0)

    def test_internal_active_bit_is_not_lru(self):
        reader=GuestKernel.__new__(GuestKernel);reader.u64=lambda _:32
        with self.assertRaisesRegex(AssertionError,'no longer on LRU'):reader.page_inode(1,0)


class AllocationOwnerTests(unittest.TestCase):
    def fixture(self,buddy_offset):
        from kernel_file_cache import allocation_owner
        reader=GuestKernel.__new__(GuestKernel)
        inode=0xffff888000010000;mapping=inode+4096;sb=inode+8192;info=inode+12288;ops=0xffffffff82032660
        row=dict(mode=0,inode=1,inode_pointer=inode,mapping_pointer=mapping,superblock_pointer=sb)
        values={mapping:inode,inode:0,inode+64:1,inode+40:sb,sb+48:ops,sb+896:info,info+buddy_offset:inode}
        reader.virtual=lambda address,size:values.get(address,0).to_bytes(size,'little')
        reader.translate=lambda address:address&0xffffff
        return reader,row,ops,values,allocation_owner

    def test_both_exact_kernel_offsets_require_real_pointer_identity(self):
        for offset in (704,736):
            reader,row,ops,values,check=self.fixture(offset)
            self.assertEqual(check(reader,row,ops,offset)['status'],'PASS')
            values[row['inode_pointer']+12288+offset]=row['inode_pointer']+8
            with self.assertRaisesRegex(AssertionError,'not actual'):check(reader,row,ops,offset)

    def test_inode_number_alone_and_wrong_filesystem_are_rejected(self):
        reader,row,ops,values,check=self.fixture(736)
        with self.assertRaisesRegex(AssertionError,'not actual'):check(reader,row,ops,704)
        values[row['superblock_pointer']+48]=ops+8
        with self.assertRaisesRegex(AssertionError,'not ext4'):check(reader,row,ops,736)


if __name__=='__main__':unittest.main()
