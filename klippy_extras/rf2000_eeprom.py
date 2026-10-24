# Access to the external I2C EEPROMs of the Renkforce RF1000/RF2000/RF2000v2
#
# The RF mainboard has two 24C256 EEPROMs on the same I2C bus as the strain
# gauge:
#   0x50  heat bed / work part z-compensation matrices ("HBS" results)
#   0x51  board type EEPROM
# This module can read the stored matrices and import them as Klipper
# bed_mesh profiles, so an existing heat bed scan made with the original
# firmware can be reused after switching to Klipper.
#
# Layout (Configuration.h of the RF firmware, EEPROM_FORMAT 7):
#   0      header format (word, == 7)
#   2      active work part matrix (word)
#   4      active heat bed matrix (word, 1..9)
#   1536*n heat bed matrix n (n=1..9), 1536*(10+m) work part matrix m
#   sector + 0  sector format (== 7)
#   sector + 2  x dimension (highest index), +4 y dimension
#   sector + 6  z micro steps used for the scan
#   sector + 28 matrix: (dimX+1)*(dimY+1) signed big endian words, x major
#               [x][0] = x position mm, [0][y] = y position mm,
#               [x][y] = z correction in Z micro steps
#
# Copyright (C) 2026  klipper-rf2000v2 contributors
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from . import bus

EEPROM_FORMAT = 7
SECTOR_SIZE = 1536
OFFSET_ACTIVE_HEAT_BED = 4
OFFSET_ACTIVE_WORK_PART = 2
SECTOR_FORMAT, SECTOR_DIM_X, SECTOR_DIM_Y, SECTOR_MICROSTEPS = 0, 2, 4, 6
SECTOR_MATRIX = 28
MAX_HEAT_BED_SECTORS = 9
WORK_PART_SECTOR_BASE = 10
READ_CHUNK = 16


def to_signed(word):
    return word - 0x10000 if word & 0x8000 else word


def parse_matrix(sector, z_steps_per_mm):
    """sector: bytes starting at the sector address.  Returns
    (xs, ys, zmm) with zmm[yi][xi] in mm, or raises ValueError."""
    def word(off):
        return (sector[off] << 8) | sector[off + 1]
    fmt = word(SECTOR_FORMAT)
    if fmt != EEPROM_FORMAT:
        raise ValueError("invalid sector format %d (expected %d)"
                         % (fmt, EEPROM_FORMAT))
    dim_x, dim_y = word(SECTOR_DIM_X), word(SECTOR_DIM_Y)
    if not (2 <= dim_x <= 40 and 2 <= dim_y <= 40):
        raise ValueError("implausible matrix dimension %dx%d" % (dim_x, dim_y))
    microsteps = word(SECTOR_MICROSTEPS)
    if microsteps not in (1, 2, 4, 8, 16, 32, 64, 128, 256):
        raise ValueError("implausible micro step value %d" % (microsteps,))
    need = SECTOR_MATRIX + 2 * (dim_x + 1) * (dim_y + 1)
    if len(sector) < need:
        raise ValueError("sector too short")
    m = [[0] * (dim_y + 1) for i in range(dim_x + 1)]
    off = SECTOR_MATRIX
    for x in range(dim_x + 1):
        for y in range(dim_y + 1):
            m[x][y] = to_signed(word(off))
            off += 2
    xs = [float(m[x][0]) for x in range(1, dim_x + 1)]
    ys = [float(m[0][y]) for y in range(1, dim_y + 1)]
    for axis, vals in (('x', xs), ('y', ys)):
        if any(b <= a for a, b in zip(vals, vals[1:])):
            raise ValueError("%s positions not increasing: %s" % (axis, vals))
    scale = 1. / (z_steps_per_mm * microsteps)
    zmm = [[m[x][y] * scale for x in range(1, dim_x + 1)]
           for y in range(1, dim_y + 1)]
    return xs, ys, zmm, microsteps


def _interp_axis(vals, v):
    """Return (i, t) so that v lies between vals[i] and vals[i+1]."""
    if v <= vals[0]:
        return 0, 0.
    if v >= vals[-1]:
        return len(vals) - 2, 1.
    for i in range(len(vals) - 1):
        if vals[i] <= v <= vals[i + 1]:
            return i, (v - vals[i]) / (vals[i + 1] - vals[i])
    return len(vals) - 2, 1.


def resample(xs, ys, zmm, x_count, y_count, bounds=None):
    """Bilinear resampling of an irregular grid onto a regular one.
    Returns (min_x, max_x, min_y, max_y, matrix[y][x])."""
    min_x, max_x, min_y, max_y = bounds or (xs[0], xs[-1], ys[0], ys[-1])
    out = []
    for yi in range(y_count):
        y = min_y + (max_y - min_y) * yi / float(y_count - 1)
        j, ty = _interp_axis(ys, y)
        row = []
        for xi in range(x_count):
            x = min_x + (max_x - min_x) * xi / float(x_count - 1)
            i, tx = _interp_axis(xs, x)
            z00, z10 = zmm[j][i], zmm[j][i + 1]
            z01, z11 = zmm[j + 1][i], zmm[j + 1][i + 1]
            z0 = z00 + (z10 - z00) * tx
            z1 = z01 + (z11 - z01) * tx
            row.append(z0 + (z1 - z0) * ty)
        out.append(row)
    return min_x, max_x, min_y, max_y, out


class RF2000EEPROM:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.i2c = bus.MCU_I2C_from_config(config, default_addr=0x50,
                                           default_speed=100000)
        self.z_full_steps_per_mm = config.getfloat('z_full_steps_per_mm', 80.,
                                                   above=0.)
        self.profile_x_count = config.getint('profile_x_count', 0, minval=0)
        self.profile_y_count = config.getint('profile_y_count', 0, minval=0)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('RF_EEPROM_INFO', self.cmd_INFO,
                               desc=self.cmd_INFO_help)
        gcode.register_command('RF_IMPORT_HBS', self.cmd_IMPORT_HBS,
                               desc=self.cmd_IMPORT_HBS_help)
    def read(self, addr, length):
        if self.i2c.get_mcu().is_fileoutput():
            return bytearray(length)
        data = bytearray()
        while length > 0:
            n = min(READ_CHUNK, length)
            params = self.i2c.i2c_read([(addr >> 8) & 0xff, addr & 0xff], n)
            data += bytearray(params['response'])
            addr += n
            length -= n
        return data
    def read_word(self, addr):
        d = self.read(addr, 2)
        return (d[0] << 8) | d[1]
    def read_sector(self, index):
        return self.read(SECTOR_SIZE * index, SECTOR_SIZE)
    cmd_INFO_help = "Show the z-compensation matrices stored by the RF firmware"
    def cmd_INFO(self, gcmd):
        header = self.read_word(0)
        if header != EEPROM_FORMAT:
            gcmd.respond_info("RF EEPROM: no valid RF firmware data (header=%d)"
                              % (header,))
            return
        active = self.read_word(OFFSET_ACTIVE_HEAT_BED)
        lines = ["RF EEPROM format %d, active heat bed matrix: %d"
                 % (header, active)]
        for n in range(1, MAX_HEAT_BED_SECTORS + 1):
            head = self.read(SECTOR_SIZE * n, 8)
            fmt = (head[0] << 8) | head[1]
            if fmt != EEPROM_FORMAT:
                continue
            dx = (head[2] << 8) | head[3]
            dy = (head[4] << 8) | head[5]
            ms = (head[6] << 8) | head[7]
            lines.append("  slot %d: %dx%d points, scanned with %d z micro"
                         " steps%s" % (n, dx, dy, ms,
                                       " (active)" if n == active else ""))
        gcmd.respond_info("\n".join(lines))
    cmd_IMPORT_HBS_help = ("Import an RF heat bed scan matrix from the"
                           " EEPROM into a bed_mesh profile")
    def cmd_IMPORT_HBS(self, gcmd):
        if self.read_word(0) != EEPROM_FORMAT:
            raise gcmd.error("RF EEPROM does not contain RF firmware data")
        slot = gcmd.get_int('SLOT', 0, minval=0, maxval=MAX_HEAT_BED_SECTORS)
        if gcmd.get_int('WORK_PART', 0):
            base = WORK_PART_SECTOR_BASE
            if not slot:
                slot = self.read_word(OFFSET_ACTIVE_WORK_PART)
        else:
            base = 0
            if not slot:
                slot = self.read_word(OFFSET_ACTIVE_HEAT_BED)
        if not 1 <= slot <= MAX_HEAT_BED_SECTORS:
            raise gcmd.error("No active matrix - pass SLOT=1..9")
        sector = self.read_sector(base + slot)
        try:
            xs, ys, zmm, ms = parse_matrix(sector, self.z_full_steps_per_mm)
        except ValueError as e:
            raise gcmd.error("RF matrix %d: %s" % (slot, str(e)))
        # at least 4 points per axis so bicubic interpolation can be used
        # (lagrange oscillates on long axes)
        xc = gcmd.get_int('X_COUNT',
                          self.profile_x_count or max(4, len(xs)),
                          minval=4, maxval=50)
        yc = gcmd.get_int('Y_COUNT',
                          self.profile_y_count or max(4, len(ys)),
                          minval=4, maxval=50)
        min_x, max_x, min_y, max_y, matrix = resample(xs, ys, zmm, xc, yc)
        # same names as M3009 / M3001: slot 1 = "default", n = "hbs_n"
        default_name = 'default' if slot == 1 else 'hbs_%d' % (slot,)
        if base:
            default_name = 'workpart' if slot == 1 else 'workpart_%d' % (slot,)
        name = gcmd.get('PROFILE', default_name)
        bed_mesh = self.printer.lookup_object('bed_mesh', None)
        if bed_mesh is None:
            raise gcmd.error("[bed_mesh] must be configured")
        from . import bed_mesh as bm
        algo = 'bicubic'
        params = {'min_x': min_x, 'max_x': max_x, 'min_y': min_y,
                  'max_y': max_y, 'x_count': xc, 'y_count': yc,
                  'mesh_x_pps': 2, 'mesh_y_pps': 2, 'algo': algo,
                  'tension': 0.2}
        z_mesh = bm.ZMesh(params, name)
        z_mesh.build_mesh(matrix)
        bed_mesh.set_mesh(z_mesh)
        bed_mesh.save_profile(name)
        flat = [z for row in zmm for z in row]
        gcmd.respond_info(
            "Imported RF matrix %d (%dx%d, %d micro steps) as bed_mesh"
            " profile '%s' (%dx%d, X %.1f..%.1f Y %.1f..%.1f, z %.3f..%.3f"
            " mm). Run SAVE_CONFIG to keep it. The RF heights are not in"
            " Klipper's absolute Z frame (0.93 mm lower on a tested"
            " printer): keep DMS_Z_OFFSET_SCAN enabled in PRINT_START,"
            " it corrects the offset." % (
                slot, len(xs), len(ys), ms, name, xc, yc, min_x, max_x,
                min_y, max_y, min(flat), max(flat)))


def load_config(config):
    return RF2000EEPROM(config)
