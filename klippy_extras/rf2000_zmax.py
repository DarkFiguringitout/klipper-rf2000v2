# Z-max endstop support for the Renkforce RF2000/RF2000v2
#
# Klipper only uses one endstop per axis.  The RF2000v2 additionally has a
# Z-max limit switch (ES4, PH1) which the original firmware uses
#   * as a safety limit switch while printing, and
#   * as the homing endstop in milling mode (the bed is moved away from the
#     tool first so a clamped work part can not hit the miller).
# This module provides both functions:
#   HOME_Z_MAX   home Z towards the Z-max switch
#   QUERY_Z_MAX         report the switch state
# and a background monitor that shuts the printer down if the switch is hit
# outside of a homing move.
#
# Copyright (C) 2026  klipper-rf2000v2 contributors
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from . import probe


class RF2000ZMax:
    def __init__(self, config):
        self.printer = printer = config.get_printer()
        self.reactor = printer.get_reactor()
        ppins = printer.lookup_object('pins')
        self.mcu_endstop = ppins.setup_pin('endstop', config.get('pin'))
        probe.LookupZSteppers(config, self.mcu_endstop.add_stepper)
        self.position = config.getfloat('position', None)
        self.speed = config.getfloat('homing_speed', 10., above=0.)
        self.second_speed = config.getfloat('second_homing_speed',
                                            self.speed / 10., above=0.)
        self.retract = config.getfloat('homing_retract_dist', 2., minval=0.)
        # must release the switch, otherwise the monitor would trip
        self.leave_dist = config.getfloat('leave_distance', 2., minval=0.5)
        self.monitor = config.getboolean('monitor', True)
        self.monitor_interval = config.getfloat('monitor_interval', 0.5,
                                                minval=0.1)
        self.homing_active = False
        self.in_home_cmd = False
        self.hits = 0
        self.timer = None
        # Klipper keeps one response handler per (message, oid): two
        # overlapping endstop queries (monitor timer + QUERY_Z_MAX) make
        # the second one fail with KeyError and shut the printer down.
        self.query_mutex = self.reactor.mutex()
        printer.register_event_handler("klippy:ready", self._handle_ready)
        printer.register_event_handler("homing:homing_move_begin",
                                       self._homing_begin)
        printer.register_event_handler("homing:homing_move_end",
                                       self._homing_end)
        gcode = printer.lookup_object('gcode')
        gcode.register_command('HOME_Z_MAX', self.cmd_HOME_Z_MAX,
                               desc=self.cmd_HOME_Z_MAX_help)
        gcode.register_command('QUERY_Z_MAX', self.cmd_QUERY_Z_MAX,
                               desc=self.cmd_QUERY_Z_MAX_help)
    def _homing_begin(self, hmove):
        self.homing_active = True
    def _homing_end(self, hmove):
        self.homing_active = False
    def _handle_ready(self):
        if self.monitor and not self.mcu_endstop.get_mcu().is_fileoutput():
            self.timer = self.reactor.register_timer(
                self._monitor, self.reactor.NOW + 2.)
    def _z_max(self):
        if self.position is not None:
            return self.position
        toolhead = self.printer.lookup_object('toolhead')
        kin = toolhead.get_kinematics()
        return kin.get_status(self.reactor.monotonic())['axis_maximum'][2]
    def _query_at(self, print_time):
        with self.query_mutex:
            return self.mcu_endstop.query_endstop(print_time)
    def _query(self):
        toolhead = self.printer.lookup_object('toolhead')
        return self._query_at(toolhead.get_last_move_time())
    def _monitor(self, eventtime):
        if self.printer.is_shutdown():
            return self.reactor.NEVER
        if self.homing_active or self.in_home_cmd:
            self.hits = 0
            return eventtime + self.monitor_interval
        toolhead = self.printer.lookup_object('toolhead')
        if 'z' not in toolhead.get_status(eventtime)['homed_axes']:
            return eventtime + self.monitor_interval
        try:
            hit = self._query_at(
                self.mcu_endstop.get_mcu().estimated_print_time(eventtime))
        except Exception:
            logging.exception("rf2000_zmax: query failed")
            return eventtime + 5.
        # two consecutive hits: ignore a single bounce of the switch
        self.hits = self.hits + 1 if hit else 0
        if self.hits >= 2 and not (self.homing_active or self.in_home_cmd):
            self.printer.invoke_shutdown(
                "Z-max endstop triggered outside of homing (bed crash"
                " protection)")
            return self.reactor.NEVER
        return eventtime + self.monitor_interval
    cmd_QUERY_Z_MAX_help = "Report the state of the Z-max endstop"
    def cmd_QUERY_Z_MAX(self, gcmd):
        gcmd.respond_info("z_max: %s" % (
            "TRIGGERED" if self._query() else "open",))
    cmd_HOME_Z_MAX_help = "Home Z towards the Z-max endstop"
    def cmd_HOME_Z_MAX(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        self.in_home_cmd = True
        try:
            self._home_z_max(gcmd, toolhead)
        except self.printer.command_error:
            # do not leave Z "homed" at the fake start position
            toolhead.get_kinematics().clear_homing_state("z")
            raise
        finally:
            # the switch is released once the leave move has finished
            toolhead.wait_moves()
            self.in_home_cmd = False
    def _home_z_max(self, gcmd, toolhead):
        phoming = self.printer.lookup_object('homing')
        kin = toolhead.get_kinematics()
        status = kin.get_status(self.reactor.monotonic())
        z_min = status['axis_minimum'][2]
        z_max = self._z_max()
        endstops = [(self.mcu_endstop, "z_max")]
        # Pretend we are far below the switch and move up to it
        pos = toolhead.get_position()
        pos[2] = z_max - 1.5 * (z_max - z_min)
        toolhead.set_position(pos, homing_axes="z")
        target = list(pos)
        target[2] = z_max
        speed = gcmd.get_float('SPEED', self.speed, above=0.)
        phoming.manual_home(toolhead, endstops, target, speed, False, True,
                            True)
        if self.retract:
            pos = toolhead.get_position()
            pos[2] = z_max
            toolhead.set_position(pos, homing_axes="z")
            pos[2] = z_max - self.retract
            toolhead.manual_move(pos, speed)
            # Like Klipper's homing: claim to be twice the retract distance
            # away so the slow pass can always reach the switch
            pos[2] = z_max - 2. * self.retract
            toolhead.set_position(pos, homing_axes="z")
            target = list(pos)
            target[2] = z_max
            phoming.manual_home(toolhead, endstops, target, self.second_speed,
                                False, True, True)
        pos = toolhead.get_position()
        pos[2] = z_max
        toolhead.set_position(pos, homing_axes="z")
        if self.leave_dist:
            pos[2] = z_max - self.leave_dist
            toolhead.manual_move(pos, speed)
        gcmd.respond_info("Z homed to Z-max endstop (Z=%.3f)" % (z_max,))


def load_config(config):
    return RF2000ZMax(config)
