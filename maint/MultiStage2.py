#! /usr/bin/python

# Multistage table builder
# (c) Peter Kankowski, 2008

##############################################################################
# This script was submitted to the PCRE project by Peter Kankowski as part of
# the upgrading of Unicode property support. The new code speeds up property
# matching many times. The script is for the use of PCRE maintainers, to
# generate the pcre_ucd.c file that contains a digested form of the Unicode
# data tables.
#
# The script should be run in the maint subdirectory, using the command
#
# ./MultiStage2.py >../pcre_ucd.c
#
# It requires three Unicode data tables, DerivedGeneralCategory.txt,
# Scripts.txt, and UnicodeData.txt, to be in the Unicode.tables subdirectory.
# The first of these is found in the "extracted" subdirectory of the Unicode
# database (UCD) on the Unicode web site; the other two are directly in the
# UCD directory.
#
# Minor modifications made to this script:
#  Added #! line at start
#  Removed tabs
#  Made it work with Python 2.4 by rewriting two statements that needed 2.5
#  Consequent code tidy
#  Adjusted data file names to take from the Unicode.tables directory
#  Adjusted global table names by prefixing _pcre_.
#  Commented out stuff relating to the casefolding table, which isn't used.
#  Corrected size calculation
#  Add #ifndef SUPPORT_UCP to use dummy tables when no UCP support is needed.
#
# The tables generated by this script are used by macros defined in
# pcre_internal.h. They look up Unicode character properties using short 
# sequences of code that contains no branches, which makes for greater speed.
#
# Conceptually, there is a table of records (of type ucd_record), containing a
# script number, character type, and offset to the character's other case for 
# every character. However, a real table covering all Unicode characters would 
# be far too big. It can be efficiently compressed by observing that many 
# characters have the same record, and many blocks of characters (taking 128
# characters in a block) have the same set of records as other blocks. This 
# leads to a 2-stage lookup process.
#
# This script constructs three tables. The _pcre_ucd_records table contains 
# one instance of every unique record that is required. The _pcre_ucd_stage1 
# table is indexed by a character's block number, and yields what is in effect
# a "virtual" block number. The _pcre_ucd_stage2 table is a table of "virtual"
# blocks; each block is indexed by the offset of a character within its own
# block, and the result is the offset of the required record.
#
# Example: lowercase "a" (U+0061) is in block 0
#          lookup 0 in stage1 table yields 0
#          lookup 97 in the first table in stage2 yields 12
#          record 12 is { 33, 5, -32 } (Latin, lowercase, upper is U+0041)
#         
# All lowercase latin characters resolve to the same record.
#
# Example: hiragana letter A (U+3042) is in block 96 (0x60)
#          lookup 96 in stage1 table yields 83
#          lookup 66 in the 83rd table in stage2 yields 348
#          record 348 is { 26, 7, 0 } (Hiragana, other letter, no other case)
#
# In these examples, no other blocks resolve to the same "virtual" block, as it
# happens, but plenty of other blocks do share "virtual" blocks.
#
# There is a fourth table, maintained by hand, which translates from the 
# individual character types such as ucp_Cc to the general types like ucp_C.
#
#  Philip Hazel, 03 July 2008
#
# 01-March-2010: Updated list of scripts for Unicode 5.2.0
# 30-April-2011: Updated list of scripts for Unicode 6.0.0
##############################################################################


import re
import string
import sys

MAX_UNICODE = 0x110000
NOTACHAR = 0xffffffff

# Parse a line of CaseFolding.txt, Scripts.txt, and DerivedGeneralCategory.txt file
def make_get_names(enum):
        return lambda chardata: enum.index(chardata[1])

#def get_case_folding_value(chardata):
#        if chardata[1] != 'C' and chardata[1] != 'S':
#                return 0
#        return int(chardata[2], 16) - int(chardata[0], 16)
        
def get_other_case(chardata):
        if chardata[12] != '':
                return int(chardata[12], 16) - int(chardata[0], 16)
        if chardata[13] != '':
                return int(chardata[13], 16) - int(chardata[0], 16)
        return 0

# Read the whole table in memory
def read_table(file_name, get_value, default_value):
        file = open(file_name, 'r')
        table = [default_value] * MAX_UNICODE
        for line in file:
                line = re.sub(r'#.*', '', line)
                chardata = map(string.strip, line.split(';'))
                if len(chardata) <= 1:
                        continue
                value = get_value(chardata)
                m = re.match(r'([0-9a-fA-F]+)(\.\.([0-9a-fA-F]+))?$', chardata[0])
                char = int(m.group(1), 16)
                if m.group(3) is None:
                        last = char
                else:
                        last = int(m.group(3), 16)            
                for i in range(char, last + 1):
                        table[i] = value
        file.close()
        return table

# Get the smallest possible C language type for the values
def get_type_size(table):
        type_size = [("uschar", 1), ("pcre_uint16", 2), ("pcre_uint32", 4),
                                 ("signed char", 1), ("pcre_int16", 2), ("pcre_int32", 4)]
        limits = [(0, 255), (0, 65535), (0, 4294967295),
                          (-128, 127), (-32768, 32767), (-2147483648, 2147483647)]
        minval = min(table)
        maxval = max(table)
        for num, (minlimit, maxlimit) in enumerate(limits):
                if minlimit <= minval and maxval <= maxlimit:
                        return type_size[num]
        else:
                raise OverflowError, "Too large to fit into C types"

def get_tables_size(*tables):
        total_size = 0
        for table in tables:
                type, size = get_type_size(table)
                total_size += size * len(table)
        return total_size

# Compress the table into the two stages
def compress_table(table, block_size):
        blocks = {} # Dictionary for finding identical blocks
        stage1 = [] # Stage 1 table contains block numbers (indices into stage 2 table)
        stage2 = [] # Stage 2 table contains the blocks with property values
        table = tuple(table)
        for i in range(0, len(table), block_size):
                block = table[i:i+block_size]
                start = blocks.get(block)
                if start is None:
                        # Allocate a new block
                        start = len(stage2) / block_size
                        stage2 += block
                        blocks[block] = start
                stage1.append(start)
        
        return stage1, stage2

# Print a table
def print_table(table, table_name, block_size = None):
        type, size = get_type_size(table)
        ELEMS_PER_LINE = 16
        
        s = "const %s %s[] = { /* %d bytes" % (type, table_name, size * len(table))
        if block_size:
                s += ", block = %d" % block_size
        print s + " */"
        table = tuple(table)
        if block_size is None:
                fmt = "%3d," * ELEMS_PER_LINE + " /* U+%04X */"
                mult = MAX_UNICODE / len(table)
                for i in range(0, len(table), ELEMS_PER_LINE):
                        print fmt % (table[i:i+ELEMS_PER_LINE] + (i * mult,))
        else:
                if block_size > ELEMS_PER_LINE:
                        el = ELEMS_PER_LINE
                else:
                        el = block_size
                fmt = "%3d," * el + "\n"
                if block_size > ELEMS_PER_LINE:
                        fmt = fmt * (block_size / ELEMS_PER_LINE)
                for i in range(0, len(table), block_size):
                        print ("/* block %d */\n" + fmt) % ((i / block_size,) + table[i:i+block_size])
        print "};\n"

# Extract the unique combinations of properties into records
def combine_tables(*tables):
        records = {}
        index = []
        for t in zip(*tables):
                i = records.get(t)
                if i is None:
                        i = records[t] = len(records)
                index.append(i)
        return index, records

def get_record_size_struct(records):
        size = 0
        structure = '/* When recompiling tables with a new Unicode version,\n' + \
        'please check types in the structure definition from pcre_internal.h:\ntypedef struct {\n'
        for i in range(len(records[0])):
                record_slice = map(lambda record: record[i], records)
                slice_type, slice_size = get_type_size(record_slice)
                # add padding: round up to the nearest power of slice_size
                size = (size + slice_size - 1) & -slice_size
                size += slice_size
                structure += '%s property_%d;\n' % (slice_type, i)
        
        # round up to the first item of the next structure in array
        record_slice = map(lambda record: record[0], records)
        slice_type, slice_size = get_type_size(record_slice)
        size = (size + slice_size - 1) & -slice_size
        
        structure += '} ucd_record; */\n\n'
        return size, structure
        
def test_record_size():
        tests = [ \
          ( [(3,), (6,), (6,), (1,)], 1 ), \
          ( [(300,), (600,), (600,), (100,)], 2 ), \
          ( [(25, 3), (6, 6), (34, 6), (68, 1)], 2 ), \
          ( [(300, 3), (6, 6), (340, 6), (690, 1)], 4 ), \
          ( [(3, 300), (6, 6), (6, 340), (1, 690)], 4 ), \
          ( [(300, 300), (6, 6), (6, 340), (1, 690)], 4 ), \
          ( [(3, 100000), (6, 6), (6, 123456), (1, 690)], 8 ), \
          ( [(100000, 300), (6, 6), (123456, 6), (1, 690)], 8 ), \
        ]
        for test in tests:
            size, struct = get_record_size_struct(test[0])
            assert(size == test[1])
            #print struct

def print_records(records, record_size):
        print 'const ucd_record _pcre_ucd_records[] = { ' + \
              '/* %d bytes, record size %d */' % (len(records) * record_size, record_size)
        records = zip(records.keys(), records.values())
        records.sort(None, lambda x: x[1])
        for i, record in enumerate(records):
                print ('  {' + '%6d, ' * len(record[0]) + '}, /* %3d */') % (record[0] + (i,))
        print '};\n'

script_names = ['Arabic', 'Armenian', 'Bengali', 'Bopomofo', 'Braille', 'Buginese', 'Buhid', 'Canadian_Aboriginal', \
 'Cherokee', 'Common', 'Coptic', 'Cypriot', 'Cyrillic', 'Deseret', 'Devanagari', 'Ethiopic', 'Georgian', \
 'Glagolitic', 'Gothic', 'Greek', 'Gujarati', 'Gurmukhi', 'Han', 'Hangul', 'Hanunoo', 'Hebrew', 'Hiragana', \
 'Inherited', 'Kannada', 'Katakana', 'Kharoshthi', 'Khmer', 'Lao', 'Latin', 'Limbu', 'Linear_B', 'Malayalam', \
 'Mongolian', 'Myanmar', 'New_Tai_Lue', 'Ogham', 'Old_Italic', 'Old_Persian', 'Oriya', 'Osmanya', 'Runic', \
 'Shavian', 'Sinhala', 'Syloti_Nagri', 'Syriac', 'Tagalog', 'Tagbanwa', 'Tai_Le', 'Tamil', 'Telugu', 'Thaana', \
 'Thai', 'Tibetan', 'Tifinagh', 'Ugaritic', 'Yi', \
# New for Unicode 5.0
 'Balinese', 'Cuneiform', 'Nko', 'Phags_Pa', 'Phoenician', \
# New for Unicode 5.1
 'Carian', 'Cham', 'Kayah_Li', 'Lepcha', 'Lycian', 'Lydian', 'Ol_Chiki', 'Rejang', 'Saurashtra', 'Sundanese', 'Vai', \
# New for Unicode 5.2
 'Avestan', 'Bamum', 'Egyptian_Hieroglyphs', 'Imperial_Aramaic', \
 'Inscriptional_Pahlavi', 'Inscriptional_Parthian', \
 'Javanese', 'Kaithi', 'Lisu', 'Meetei_Mayek', \
 'Old_South_Arabian', 'Old_Turkic', 'Samaritan', 'Tai_Tham', 'Tai_Viet', \
# New for Unicode 6.0.0
 'Batak', 'Brahmi', 'Mandaic'  
 ]
 
category_names = ['Cc', 'Cf', 'Cn', 'Co', 'Cs', 'Ll', 'Lm', 'Lo', 'Lt', 'Lu',
  'Mc', 'Me', 'Mn', 'Nd', 'Nl', 'No', 'Pc', 'Pd', 'Pe', 'Pf', 'Pi', 'Po', 'Ps',
  'Sc', 'Sk', 'Sm', 'So', 'Zl', 'Zp', 'Zs' ]

test_record_size()

script = read_table('Unicode.tables/Scripts.txt', make_get_names(script_names), script_names.index('Common'))
category = read_table('Unicode.tables/DerivedGeneralCategory.txt', make_get_names(category_names), category_names.index('Cn'))
other_case = read_table('Unicode.tables/UnicodeData.txt', get_other_case, 0)
# case_fold = read_table('CaseFolding.txt', get_case_folding_value, 0)

table, records = combine_tables(script, category, other_case)
record_size, record_struct = get_record_size_struct(records.keys())

# Find the optimum block size for the two-stage table
min_size = sys.maxint
for block_size in [2 ** i for i in range(5,10)]:
        size = len(records) * record_size
        stage1, stage2 = compress_table(table, block_size)
        size += get_tables_size(stage1, stage2)
        #print "/* block size %5d  => %5d bytes */" % (block_size, size)
        if size < min_size:
                min_size = size
                min_stage1, min_stage2 = stage1, stage2
                min_block_size = block_size

print "#ifdef HAVE_CONFIG_H"
print "#include \"config.h\""
print "#endif"
print
print "#include \"pcre_internal.h\""
print
print "/* Unicode character database. */"
print "/* This file was autogenerated by the MultiStage2.py script. */"
print "/* Total size: %d bytes, block size: %d. */" % (min_size, min_block_size)
print
print "/* The tables herein are needed only when UCP support is built */"
print "/* into PCRE. This module should not be referenced otherwise, so */"
print "/* it should not matter whether it is compiled or not. However */"
print "/* a comment was received about space saving - maybe the guy linked */"
print "/* all the modules rather than using a library - so we include a */"
print "/* condition to cut out the tables when not needed. But don't leave */"
print "/* a totally empty module because some compilers barf at that. */"
print "/* Instead, just supply small dummy tables. */"
print
print "#ifndef SUPPORT_UCP"
print "const ucd_record _pcre_ucd_records[] = {{0,0,0 }};"
print "const uschar _pcre_ucd_stage1[] = {0};"
print "const pcre_uint16 _pcre_ucd_stage2[] = {0};"
print "#else"
print
print record_struct
print_records(records, record_size)
print_table(min_stage1, '_pcre_ucd_stage1')
print_table(min_stage2, '_pcre_ucd_stage2', min_block_size)
print "#if UCD_BLOCK_SIZE != %d" % min_block_size
print "#error Please correct UCD_BLOCK_SIZE in pcre_internal.h"
print "#endif"
print "#endif  /* SUPPORT_UCP */"

"""

# Three-stage tables:

# Find the optimum block size for 3-stage table
min_size = sys.maxint
for stage3_block in [2 ** i for i in range(2,6)]:
        stage_i, stage3 = compress_table(table, stage3_block)
        for stage2_block in [2 ** i for i in range(5,10)]:
                size = len(records) * 4
                stage1, stage2 = compress_table(stage_i, stage2_block)
                size += get_tables_size(stage1, stage2, stage3)
                # print "/* %5d / %3d  => %5d bytes */" % (stage2_block, stage3_block, size)
                if size < min_size:
                        min_size = size
                        min_stage1, min_stage2, min_stage3 = stage1, stage2, stage3
                        min_stage2_block, min_stage3_block = stage2_block, stage3_block

print "/* Total size: %d bytes" % min_size */
print_records(records)
print_table(min_stage1, 'ucd_stage1')
print_table(min_stage2, 'ucd_stage2', min_stage2_block)
print_table(min_stage3, 'ucd_stage3', min_stage3_block)

"""
