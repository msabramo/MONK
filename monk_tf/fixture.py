# -*- coding: utf-8 -*-
#
# MONK automated test framework
#
# Copyright (C) 2013 DResearch Fahrzeugelektronik GmbH
# Written and maintained by MONK Developers <project-monk@dresearch-fe.de>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version
# 3 of the License, or (at your option) any later version.
#

"""
Instead of creating :py:class:`~monk_tf.dev.Device` and
:py:class:`~monk_tf.conn.AConnection` objects by yourself, you can also choose
to put corresponding data in a separate file and let this layer handle the
object concstruction and destruction for you. Doing this will probably make
your test code look more clean, keep the number of places where you need to
change something as small as possible, and lets you reuse data that you already
have described.

A hello world test with it looks like this::

    import nose
    from monk_tf import fixture

    def test_hello():
        ''' say hello
        '''
        # set up
        h = fixture.Fixture('target_device.cfg')
        expected_out = "hello"
        # execute
        out = h.devs[0].cmd('echo "hello"')
        # assert
        nose.tools.eq_(expected_out, out)
        # tear down
        h.tear_down()

When using this layer setting up a device only takes one line of code. The rest
of the information is in the ``target_device.cfg`` file. :term:`MONK` currently 
comes with one text format parser predefined, which is the
:py:class:`~monk_tf.fixture.XiniParser`. ``Xini`` is short for
:term:`extended INI`. You may, however, use any data format you want, if you
extend the :py:class:`~monk_tf.fixture.AParser` class accordingly.

An example ``Xini`` data file might look like this::

    [device1]
        type=Device
        [[serial1]]
            type=SerialConnection
            port=/dev/ttyUSB1
            user=example
            password=secret

As you can see it looks like an :term:`INI` file. There are sections,
consisting of a title enclosed in squared brackets (``[]``) and lists of
properties, consisting of key-value pairs separated by equality signs (``=``).
The unusual part is that the section *serial1* is surrounded by two pairs of
squared brackets (``[]``). This is the specialty of this format indicating that
*serial1* is a subsection of *device1* and therefore is a nested section. This
nesting can be done unlimited, by surrounding a section with more and more
pairs of squared brackets (``[]``) according to the level of nesting intended.
In this example *serial1* belongs to *device1* and the types indicate the
corresponding :term:`MONK` object to be created.

Classes
-------
"""

import os
import os.path as op
import sys
import logging
import collections

import configobj as config

import conn
import dev

logger = logging.getLogger(__name__)

############
#
# Exceptions
#
############

class AFixtureException(Exception):
    """ Base class for exceptions of the fixture layer.

    If you want to make sure that you catch all exceptions that are related
    to this layer, you should catch *AFixtureExceptions*. This also means
    that if you extend this list of exceptions you should inherit from this
    exception and not from :py:exc:`~exceptions.Exception`.
    """
    pass

class CantHandleException(AFixtureException):
    """ if none of the devices is able to handle a cmd_any() call
    """
    pass

class AParseException(AFixtureException):
    """ Base class for exceptions concerning parsing errors.
    """
    pass

class CantParseException(AFixtureException):
    """ is raised when a Fixture cannot parse a given file.
    """
    pass

class NoPropsException(AFixtureException):
    """ is raised when
    """
    pass

class NoDeviceException(AFixtureException):
    """ is raised when a :py:clas:`~monk_tf.fixture.Fixture` requires a device but has none.
    """
    pass

class WrongNameException(AFixtureException):
    """ is raised when no devs with a given name could be found.
    """
    pass

##############################################################
#
# Fixture Classes - creates MONK objects based on dictionaries
#
##############################################################

class Fixture(object):
    """ Creates :term:`MONK` objects based on dictionary like objects.

    This is the class that provides the fundamental feature of this layer. It
    reads data files by trying to parse them via its list of known parsers and
    if it succeeds, it creates :term:`MONK` objects based on the configuration
    given by the data file. Most likely these objects are one or more
    :py:class:`~monk_tf.dev.Device` objects that have at least one
    :py:class:`~monk_tf.conn.AConnection` object each. If more than one
    :term:`fixture file` is read containing the same name on the highest level,
    then the latest data gets used. This does not work on lower levels of
    nesting, though. If you attempt to overwrite lower levels of nesting, what
    actually happens is that the highest layer gets overwritten and you lose
    the data that was stored in the older objects. This is simply how
    :py:meth:`set.update` works.

    One source of data (either a file name or a child class of
    :py:class:`~monk_tf.fixture.AParser`) can be given to an object of this
    class by its constructer, others can be added afterwards with the
    :py:meth:`~monk_tf.fixture.Fixture.read` method. An example looks like
    this::

        import monk_tf.fixture as mf

        fixture = mf.Fixture('/etc/monk_tf/default_devices.cfg')
                .read('~/.monk/default_devices.cfg')
                # can also be a parser object
                .read(XiniParser('~/testsuite12345/suite_devices.cfg'))

    """

    _DEFAULT_CLASSES = {
        "Device" : dev.Device,
        "HydraDevice" : dev.Hydra,
        "SerialConnection" : conn.SerialConn,
        "SshConnection" : conn.SshConn,
    }

    _DEFAULT_DEBUG_SOURCE = "MONK_DEBUG_SOURCE"

    def __init__(self, call_location, name=None, classes=None,
            lookfordbgsrc=True, filename="fixture.cfg", auto_search=True):
        """

        :param call_location: the __file__ from where this is called.

        :param name: The name of this object.

        :param parsers: An :python:term:`iterable` of
                        :py:class:`~monk_tf.fixture.AParser` classes to be used
                        for parsing a given
                        :py:attr:`~monk_tf.fixture.Fixture.source`.

        :param classes: A :py:class:`dict` of classes to class names. Used for
                        parsing the type attribute in
                        :term:`fixture files<fixture file>`.

        :param lookfordbgsrc: If True an environment variable is looked for to
                              read a local debug config. If False it won't be
                              looked for.

        :param filename: the name of the file which contains the configuration.

        :param auto_search: if true, it will automatically search and load
                            fixture files.
        """
        self.call_location = op.dirname(op.abspath(call_location))
        self._logger = logging.getLogger("{}:{}".format(
            __name__,
            name or self.__class__.__name__,
        ))
        self.devs = []
        self._devs_dict = {}
        self.classes = classes or self._DEFAULT_CLASSES
        self.props = config.ConfigObj()
        self.filename = filename
        self.auto_search = auto_search
        # look if the user has a default config in his home dir
        if auto_search:
            self.log("autosearching for fixture files...")
            home_fixture = op.expanduser(op.join("~", self.filename))
            if op.exists(home_fixture):
                self.read(home_fixture)
            # starting from root load all fixtures from parent directories
            self.log("location:{}".format(self.call_location))
            self.log("parent_dirs:" + str(list(self._parent_dirs(self.call_location))))
            for p in reversed(list(self._parent_dirs(self.call_location))):
                fixture_file = op.join(p, self.filename)
                if op.exists(fixture_file):
                    self.read(fixture_file)
        else:
            self.log("auto search deactivated, loaded without looking for fixture files")

    @property
    def name(self):
        return self._logger.name

    @name.setter
    def name(self, new_name):
        self._logger.name = new_name

    def _parent_dirs(self, path):
        """ generate parent directories for path
        """
        while True:
            yield path
            mem = op.dirname(path)
            if path == mem:
                break
            else:
                path = mem


    def read(self, source):
        """ Read more data, either as a file name or as a parser.

        :param source: the data source; either a file name or a
                       :py:class:`~monk_tf.fixture.AParser` child class
                       instance.

        :return: self
        """
        self._logger.debug("read: " + str(source))
        self.tear_down()
        self.props.merge(config.ConfigObj(source))
        self._initialize()
        return self

    def _initialize(self):
        """ Create :term:`MONK` objects based on self's properties.
        """
        self._logger.debug("initialize with props: " + str(self.props))
        if self.props:
            self.devs = [self._parse_section(d, self.props[d]) for d in self.props.viewkeys()]
        else:
            raise NoPropsException("have you created any fixture files?")

    def _parse_section(self, name, section):
        self._logger.debug("parse_section({},{},{})".format(
            str(name),
            type(section).__name__,
            list(section.keys())
        ))
        # TODO section parsing should be wrapped in handlers
        #      so that they can be extended without overwrites
        sectype = self.classes[section.pop("type")]
        if "conns" in section:
            cs = section.pop("conns")
            section["conns"] = [self._parse_section(s, cs[s]) for s in cs]
        if "bcc" in section:
            self.log("DEPRECATED: Use bctrl instead of bcc")
            bs = section.pop("bcc")
            section["bcc"] = self._parse_section("bcc", bs)
        if "bctrl" in section:
            bs = section.pop("bctrl")
            section["bcc"] = self._parse_section("bctrl", bs)
        section["name"] = name
        self.log("load section:" + str(sectype) + "," + str(section))
        return sectype(**section)

    def cmd_first(self, msg, expect=None, timeout=30, login_timeout=None):
        """ call :py:meth:`cmd` from first :py:class:`~monk_tf.device.Device`
        """
        self.log("cmd_first({},{},{},{})".format(
            msg, expect, timeout, login_timeout))
        try:
            return self.devs[0].cmd(msg)
        except IndexError:
            raise NoDeviceException("this fixture has no device loaded")

    def cmd_any(self, msg, expect=None, timeout=30, login_timeout=None):
        self.log("cmd_any({},{},{},{})".format(
            msg, expect, timeout, login_timeout))
        if not self.devs:
            self._logger.warning("fixture has no devices for sending commands to")
        for dev in self.devs:
            try:
                self.log("send cmd '{}' to device '{}'".format(
                    msg.encode("unicode-escape"),
                    dev,
                ))
                return dev.cmd(
                        msg=msg,
                        expect=expect,
                        timeout=timeout,
                        login_timeout=login_timeout,
                )
            except Exception as e:
                self._logger.exception(e)
            raise CantHandleException(
                    "fixt:'{}',devs:{},could not send cmd '{}'".format(
                        self.name,
                        map(str, self.devs),
                        msg.encode('unicode-escape'),
            ))

    def cmd_all(self, msg, expect=None, timeout=30, login_timeout=None):
        self.log("cmd_any({},{},{},{})".format(
            msg, expect, timeout, login_timeout))
        if not self.devs:
            self._logger.warning("fixture has no devices for sending commands to")
        for dev in self.devs:
            self.log("send cmd '{}' to device '{}'".format(
                msg.encode("unicode-escape"),
                dev,
            ))
            return dev.cmd(
                    msg=msg,
                    expect=expect,
                    timeout=timeout,
                    login_timeout=login_timeout,
            )

    def get_dev(self, which):
        try:
            return self.devs[which]
        except TypeError:
            try:
                return self._devs_dict[which]
            except KeyError:
                names = []
                for dev in self.devs:
                    if dev.name == which:
                        self._devs_dict[which] = dev
                        return dev
                    else:
                        names.append(dev.name)
                raise WrongNameException("Couldn't retreive connection with name '{}'. Available names are: {}".format(which, names))

    def reset_config_all(self):
        if not self.devs:
            self._logger.warning("fixture has no devices for sending commands to")
        for dev in self.devs:
            dev.reset_config()


    def log(self, msg):
        self._logger.debug(msg)

    def tear_down(self):
        """ Can be used for explicit destruction of managed objects.

        This should be called in every :term:`test case` as the last step.

        """
        self.log("teardown")
        for device in self.devs:
            device.close_all()
        self.devs = []

    def __str__(self):
        return "{cls}.devs:{devs}".format(
                cls=self.__class__.__name__,
                devs=[str(d) for d in self.devs],
        )

    def __enter__(self):
        self.log("__enter__")
        return [self] + list(self.devs)

    def __exit__(self, exception_type, exception_val, trace):
        self.log("__exit__")
        self.tear_down()
