#-*- coding: utf-8 -*-
'''
Created on 28 сент. 2010

@author: ivan
'''

import os
import time
import thread
import urllib
import logging
import threading

import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst

from foobnix.fc.fc import FC
from foobnix.util.id3_util import decode_cp866
from foobnix.regui.engine import MediaPlayerEngine
from foobnix.util.plsparser import get_radio_source
from foobnix.util.const import STATE_STOP, STATE_PLAY, STATE_PAUSE, FTYPE_RADIO

Gst.init("")


class GStreamerEngine(MediaPlayerEngine):
    NANO_SECONDS = 1000000000
    SPECT_BANDS = 10
    AUDIOFREQ = 44100

    def __init__(self, controls):
        MediaPlayerEngine.__init__(self, controls)
        self.bean = None
        #self.player = self.gstreamer_player()
        self.position_sec = 0
        self.duration_sec = 0

        self.prev_path = None

        self.equalizer = None

        self.current_state = STATE_STOP
        self.remembered_seek_position = 0
        self.error_counter = 0
        self.player = self.gstreamer_player()

    def get_state(self):
        return self.current_state

    def set_state(self, state):
        self.current_state = state

    def gstreamer_player(self):
        playbin = Gst.Pipeline()
        self.fsource = Gst.ElementFactory.make("filesrc", "fsource")
        self.init_hsource()
        volume = Gst.ElementFactory.make("volume", "volume")
        audioconvert = Gst.ElementFactory.make("audioconvert", "audioconvert")
        audiosink = Gst.ElementFactory.make("autoaudiosink", "autoaudiosink")
        self.decodebin = Gst.ElementFactory.make("decodebin", "decode")
        self.equalizer = Gst.ElementFactory.make('equalizer-10bands', 'equalizer')

        #self.spectrum = Gst.ElementFactory.make('spectrum', 'spectrum')
        #self.spectrum.set_property("bands", self.SPECT_BANDS)
        #self.spectrum.set_property("threshold", -80)
        #self.spectrum.set_property("message-phase", True)

        def on_new_decoded_pad(dbin, pad):
            pad.link(audioconvert.get_static_pad("sink"))

        self.decodebin.connect("pad-added", on_new_decoded_pad)

        playbin.add(self.decodebin)
        playbin.add(volume)
        playbin.add(audioconvert)
        playbin.add(audiosink)
        #playbin.add(self.spectrum)
        playbin.add(self.equalizer)
        audioconvert.link(volume)
        #audioconvert.link(self.spectrum)
        #self.spectrum.link(volume)
        volume.link(self.equalizer)
        self.equalizer.link(audiosink)

        bus = playbin.get_bus()
        bus.add_signal_watch()
        bus.enable_sync_message_emission()
        bus.connect("message", self.on_message)
        bus.connect("sync-message::element", self.on_sync_message)

        return playbin

    def realign_eq(self):
        if FC().is_eq_enable:
            pre = self.controls.eq.get_preamp()
            bands = self.controls.eq.get_bands()
            self.set_all_bands(pre, bands, force=True)
        else:
            self.set_all_bands(0, [0] * 10, force=True)

    def init_hsource(self):
        self.hsource = Gst.ElementFactory.make("souphttpsrc", "hsource")
        self.hsource.set_property("user-agent", "Fooobnix music player")
        self.hsource.set_property("automatic-redirect", "false")

    def notify_init(self, duration_int):
        logging.debug("Pre init thread: " + str(duration_int))

    def notify_playing(self, position_int, duration_int, sec):
        #LOG.debug("Notify playing", position_int)
        self.position_sec = position_int / self.NANO_SECONDS
        self.duration_sec = duration_int / self.NANO_SECONDS
        self.controls.notify_playing(self.position_sec, self.duration_sec, self.bean, sec)

    def notify_eos(self):
        logging.debug("Notify eos, STOP State")
        self.controls.notify_eos()
        self.set_state(STATE_STOP)

    def notify_title(self, text):
        if not text:
            return
        if self.bean.type == FTYPE_RADIO:
            "notify radio playing"
            self.controls.notify_title(self.bean, text)

    def notify_error(self, msg):
        logging.debug("Notify error, STOP state")
        self.set_state(STATE_STOP)
        self.controls.notify_error(msg)

    def record_radio(self, bean):
        if os.path.isfile(self.radio_path):
            file_name = os.path.join("/tmp", os.path.splitext(os.path.basename(self.radio_path))[0] + ".ogg")
        else:
            file_name = os.path.join("/tmp", "radio_record.ogg")

        #self.pipeline = Gst.parse_launch("""souphttpsrc location=%s ! tee name=t ! queue ! decodebin2 ! audioconvert ! audioresample ! autoaudiosink  t. ! queue ! filesink location=%s""" % (self.radio_rec_path, file_name))
        self.pipeline = Gst.parse_launch(
            """alsasrc ! audioconvert ! vorbisenc bitrate=128000 ! oggmux ! filesink location=%s""" % file_name)
        self.pipeline.set_state(Gst.State.PLAYING)

    def play(self, bean):
        if not bean or not bean.path:
            logging.error("Bean or path is None")
            return None

        self.bean = bean

        path = bean.path

        self.state_stop(show_in_tray=False)
        self.player.set_state(Gst.State.NULL)

        if hasattr(self, "pipeline"):
            self.pipeline.set_state(Gst.State.NULL)

        if path.startswith("http://"):
            self.radio_path = get_radio_source(path)
            logging.debug("Try To play path " + self.radio_path)
            uri = self.radio_path

            if not self.bean.type == FTYPE_RADIO:
                self.notify_title(uri)
        else:
            uri = path

        logging.info("Gstreamer try to play " + uri)

        self.fsource.set_state(Gst.State.NULL)
        self.hsource.set_state(Gst.State.NULL)
        self.fsource.unlink(self.decodebin)
        self.hsource.unlink(self.decodebin)
        if self.player.get_by_name("fsource"):
            self.player.remove(self.fsource)
        if self.player.get_by_name("hsource"):
            self.player.remove(self.hsource)
        if uri.startswith("http://"):
            logging.debug("Set up hsource")
            self.init_hsource()
            if FC().proxy_enable and FC().proxy_url:
                logging.debug("gst proxy set up")
                self.hsource.set_property("proxy", FC().proxy_url)
                self.hsource.set_property("proxy-id", FC().proxy_user)
                self.hsource.set_property("proxy-pw", FC().proxy_password)

            self.player.add(self.hsource)
            self.hsource.link(self.decodebin)
            self.player.get_by_name("hsource").set_property("location", uri)
            self.hsource.set_state(Gst.State.READY)
        else:
            logging.debug("Set up fsource")
            self.player.add(self.fsource)
            self.fsource.link(self.decodebin)
            self.player.get_by_name("fsource").set_property("location", uri)
            self.fsource.set_state(Gst.State.READY)

        self.realign_eq()

        self.state_play()

        if self.remembered_seek_position:
            self.wait_for_seek()
            self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, self.remembered_seek_position)
        else:
            if bean.start_sec and bean.start_sec != '0':
                self.wait_for_seek()
                self.seek_seconds(bean.start_sec)

        self.remembered_seek_position = 0
        '''trick to mask bug with ape playing
        if get_file_extension(bean.path) == '.ape' and bean.start_sec and bean.start_sec != '0':
            self.volume(0)
            threading.Timer(1.8, lambda: self.volume(FC().volume)).start()
        else:
            self.volume(FC().volume)'''

        logging.debug(
            "current state before thread " + str(self.get_state()) + " thread_id: " + str(self.play_thread_id))
        self.play_thread_id = thread.start_new_thread(self.playing_thread, ())
        self.pause_thread_id = False

    def wait_for_seek(self):
        while True:
            try:
                init_time = time.time()
                self.player.query_position(Gst.Format.TIME)
                logging.debug("Wait for seek: " + str(time.time() - init_time))
                return
            except Exception as e:
                continue

    def set_all_bands(self, pre, values, force=False):
        if FC().is_eq_enable or force:
            for i, value in enumerate(values):
                real = float(value) + pre
                if real >= 12:
                    real = 12
                if real <= -12:
                    real = -12
                self.equalizer.set_property("band%s" % i, real)

    def get_position_seek_ns(self):
        try:
            position = self.player.query_position(Gst.Format(Gst.Format.TIME))
            #print ("get_position_seek_ns", position)
            return position[1]
        except Exception, e:
            logging.warn("GET query_position: " + str(e))
            return - 1

    def get_duration_seek_ns(self):
        try:
            position = self.player.query_duration(Gst.Format(Gst.Format.TIME))
            #print ("get_duration_seek_ns", position)
            return position[1]
        except Exception, e:
            logging.warn("GET query_duration: " + str(e))
            return - 1

    def playing_thread(self):
        if not self.play_thread_id:
            self.play_thread_id = 1
        thread_id = self.play_thread_id
        sec = 0

        logging.debug("current state in thread: " + str(self.get_state()))

        attemps = 5
        for i in xrange(attemps):
            if thread_id == self.play_thread_id and i < attemps:
                time.sleep(0.2)
                duration_int = self.get_duration_seek_ns()
                if duration_int <= 0:
                    time.sleep(0.2)
                    continue
                self.notify_init(duration_int)
                break
            else:
                break

        if self.bean.duration_sec > 0:
            duration_int = float(self.bean.duration_sec) * self.NANO_SECONDS

        logging.debug("current state before while " + str(self.get_state()))

        self.set_state(STATE_PLAY)

        while thread_id == self.play_thread_id:
            if self.pause_thread_id:
                time.sleep(0.1)
                continue
            try:
                position_int = self.get_position_seek_ns()
                if position_int > 0 and self.bean.start_sec > 0:
                    position_int -= float(self.bean.start_sec) * self.NANO_SECONDS
                    #logging.debug(str(position_int) + str(self.bean.start_sec) + str(duration_int))
                    if (position_int + self.NANO_SECONDS) > duration_int:
                        self.notify_eos()

                if self.get_state() == STATE_PLAY:
                    sec += 1

                self.notify_playing(position_int, duration_int, sec)
            except Exception, e:
                logging.info("Playing thread error... " + str(e))

            time.sleep(1)

    def seek(self, percent, offset=0):
        if not self.bean:
            return None
        seek_ns = self.duration_sec * (percent + offset) / 100 * self.NANO_SECONDS

        if self.bean.start_sec > 0:
            seek_ns += float(self.bean.start_sec) * self.NANO_SECONDS

        self.player.seek_simple(Gst.Format(Gst.Format.TIME), Gst.SeekFlags.FLUSH, seek_ns)

    def seek_seconds(self, seconds):
        if not seconds:
            return
        logging.info("Start with seconds " + str(seconds))
        seek_ns = (float(seconds) + 0.0) * self.NANO_SECONDS
        logging.info("SEC SEEK SEC " + str(seek_ns))
        self.player.seek_simple(Gst.Format(Gst.Format.TIME), Gst.SeekFlags.FLUSH, seek_ns)

    def seek_ns(self, ns):
        if not ns:
            return
        logging.info("SEC ns " + str(ns))
        self.player.seek_simple(Gst.Format(Gst.Format.TIME), Gst.SeekFlags.FLUSH, ns)

    def volume(self, percent):
        value = percent / 100.0
        try:
            self.player.set_property('volume', value)
        except:
            self.player.get_by_name("volume").set_property('volume', value)
            #self.player.get_by_name("volume").set_property('volume', value + 0.0)

    def state_play(self):
        self.pause_thread_id = False
        self.player.set_state(Gst.State.PLAYING)
        self.current_state = STATE_PLAY
        self.on_chage_state()
        if hasattr(self, 'pipeline'):
            if Gst.STATE_PAUSED in self.pipeline.get_state()[1:]:
                self.pipeline.set_state(Gst.State.PLAYING)

    def get_current_percent(self):
        duration = self.get_duration_seek_ns()
        postion = self.get_position_seek_ns()
        return postion * 100.0 / duration

    def seek_up(self, offset=3):
        self.seek(self.get_current_percent(), offset)
        logging.debug("SEEK UP")

    def seek_down(self, offset=-3):
        self.seek(self.get_current_percent(), offset)
        logging.debug("SEEK DOWN")

    def state_stop(self, remember_position=False, show_in_tray=True):
        if remember_position:
            self.player.set_state(Gst.State.PAUSED)
            time.sleep(0.1)
            self.remembered_seek_position = self.get_position_seek_ns()
            self.pause_thread_id = True
        else:
            self.play_thread_id = None

        self.player.set_state(Gst.State.PAUSED)
        self.set_state(STATE_STOP)

        if show_in_tray:
            self.on_chage_state()
        logging.debug("state STOP")
        if hasattr(self, 'pipeline'):
            if Gst.State.PLAYING in self.pipeline.get_state()[1:]:
                self.controls.record.set_active(False)  # it will call "on toggle" method from self.record

    def state_pause(self, show_in_tray=True):
        self.player.set_state(Gst.State.PAUSED)
        self.set_state(STATE_PAUSE)
        if show_in_tray:
            self.on_chage_state()
        '''if hasattr(self, 'pipeline'):
            print "in pause", self.pipeline.get_state()[1:]
            if Gst.State.PLAYING in self.pipeline.get_state()[1:]:
                self.pipeline.set_state(Gst.State.PAUSED)
                print "pause after", self.pipeline.get_state()[1:]
            elif Gst.State.PAUSED in self.pipeline.get_state()[1:]:
                self.pipeline.set_state(Gst.State.PLAYING)'''

    def state_play_pause(self):
        if self.get_state() == STATE_PLAY:
            self.state_pause()
        else:
            self.state_play()

    def on_chage_state(self):
        self.controls.on_chage_player_state(self.get_state(), self.bean)

    def on_sync_message(self, bus, message):
        struct = message.get_structure()
        if struct is None:
            return
        if struct.get_name() == "spectrum":
            print ("spectrum data")
            magnitude = struct.get_value("magnitude")
            phase = struct.get_value("phase")
            print (magnitude, phase)
        else:
            self.controls.movie_window.draw_video(message)

    def on_message(self, bus, message):
        type = message.type
        struct = message.get_structure()

        if type == Gst.MessageType.BUFFERING:
            return

        if type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logging.warn("Error: " + str(err) + str(debug) + str(err.domain) + str(err.code))

            if self.error_counter > 1 and err.code != 1:
                self.notify_error(str(err))
                self.error_counter = 0
                self.state_stop()
            else:
                logging.warning("Error ocured, retry")
                self.error_counter += 1
                self.play(self.bean)

        elif type in [Gst.MessageType.STATE_CHANGED, Gst.MessageType.STREAM_STATUS]:
            if (self.bean and self.bean.type == FTYPE_RADIO and
                    struct.has_field("new-state") and
                    struct.get_enum('old-state', Gst.State) == Gst.State.READY and
                    struct.get_enum('new-state', Gst.State) == Gst.State.NULL):
                logging.info("Reconnect")
                self.play(self.bean)
                return

        if type == Gst.MessageType.TAG and message.parse_tag():
            self.error_counter = 0

            if struct.has_field("taglist"):
                taglist = struct.get_value("taglist")
                title = taglist.get_string("title")[1]
                if not title:
                    title = ""
                title = decode_cp866(title)
                text = title

                if taglist.get_string('artist')[0]:
                    artist = taglist.get_string('artist')[1]
                    artist = decode_cp866(artist)
                    text = artist + " - " + text
                if self.bean.type == FTYPE_RADIO and taglist.get_uint('bitrate')[0]:
                    text = text + " (bitrate: " + str(taglist.get_uint('bitrate')[1]) + ")"

                self.notify_title(text)

        elif type == Gst.MessageType.EOS:
            self.error_counter = 0
            logging.info("MESSAGE_EOS")
            self.notify_eos()
