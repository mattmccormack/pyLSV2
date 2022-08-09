#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""low level communication functions for LSV2"""
import logging
import socket
import struct
from typing import Tuple, Union

from .const import CMD, RSP
from .dat_cls import TransmissionError


class LLLSV2Com:
    """Implementation of the low level communication functions for sending and
    receiving LSV2 telegrams via TCP"""

    DEFAULT_PORT = 19000
    # Default port for LSV2 on control side

    DEFAULT_BUFFER_SIZE = 256
    # Default size of send and receive buffer

    def __init__(self, hostname: str, port: int = 19000, timeout: float = 15.0):
        """Set connection parameters

        :param hostname: ip or hostname of control.
        :param port: port number, defaults to 19000.
        :param timeout: number of seconds for time out of connection.

        :raises socket.gaierror: Hostname could not be resolved
        :raises socket.error: could not create socket
        """
        self._logger = logging.getLogger("LSV2 TCP")

        try:
            self._host_ip = socket.gethostbyname(hostname)
        except socket.gaierror:
            logging.error("there was an error resolving the host")
            raise

        self._port = self.DEFAULT_PORT
        if port > 0:
            self._port = port

        self._buffer_size = -1
        self.set_buffer_size(LLLSV2Com.DEFAULT_BUFFER_SIZE)

        try:
            self._tcpsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._tcpsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._tcpsock.settimeout(timeout)
        except socket.error as err:
            self._logger.error("socket creation failed with error %s", err)
            raise

        self._is_connected = False
        self._last_lsv2_response = RSP.NONE
        self._last_error = TransmissionError()

        self._logger.debug(
            "Socket successfully created, host %s was resolved to IP %s",
            hostname,
            self._host_ip,
        )

    @property
    def last_response(self):
        return self._last_lsv2_response

    @property
    def last_error(self) -> tuple:
        return self._last_error

    def connect(self):
        """
        Establish connection to control

        :raise socket.timeout: Exception if connection times out.
        """
        try:
            self._tcpsock.connect((self._host_ip, self._port))
        except socket.timeout:
            self._logger.error("could not connect to control")
            raise

        self._is_connected = True
        self._last_lsv2_response = RSP.NONE
        self._last_error = TransmissionError()

        self._logger.debug("Connected to host %s at port %s",
                           self._host_ip, self._port)

    def disconnect(self):
        """
        Close connection

        :raise socket.timeout: Exception if connection times out.
        """
        try:
            if self._tcpsock is not None:
                self._tcpsock.close()
        except socket.timeout:
            self._logger.error("error while closing socket")
            raise

        self._is_connected = False
        self._last_lsv2_response = RSP.NONE
        self._last_error = TransmissionError()

        self._logger.debug("Connection to %s closed", self._host_ip)

    def set_buffer_size(self, buffer_size: int) -> bool:
        """
        set the size of the send and receive buffer. the size has to be established
        during connection setup. buffer size has to be at least 8 so command and length
        fit into the telegram.

        :param buffer_size: new size of send and receive buffer
        """
        if buffer_size < 8:
            self._buffer_size = self.DEFAULT_BUFFER_SIZE
            self._logger.warning(
                "size of receive buffer to small, set to default of %d bytes",
                self._buffer_size,
            )
            return False
        self._buffer_size = buffer_size
        return True

    def get_buffer_size(self) -> int:
        """return current send/receive buffer size"""
        return self._buffer_size

    def telegram(
        self,
        command: Union[CMD, RSP],
        payload: bytearray = bytearray(),
        wait_for_response: bool = True,
    ) -> Tuple[RSP, bytearray]:
        """
        Send LSV2 telegram and receive response if necessary.

        :param command: command string
        :param payload: command payload
        :param wait_for_response: switch for waiting for response from control.
        :raise Exception: if connection is not already open or error during transmission.
        :raise OverflowError: if payload is to long for current buffer size
        :raise Exception: ToDo1
        :raise Exception: ToDo2
        :raise Exception: ToDo3
        """
        if self._is_connected is False:
            raise Exception("connection is not open!")

        if payload is None or len(payload) == 0:
            payload = bytearray()
            payload_length = 0
        else:
            payload_length = len(payload)

        self._last_lsv2_response = RSP.NONE

        telegram = bytearray()
        # L -> unsigned long -> 32 bit
        telegram.extend(struct.pack("!L", payload_length))
        telegram.extend(map(ord, command))

        if len(payload) > 0:
            telegram.extend(payload)
        self._logger.debug(
            "telegram to transmit: command %s payload length %d bytes data: %s",
            command,
            payload_length,
            telegram,
        )
        if len(telegram) >= self._buffer_size:
            raise OverflowError(
                "telegram to long for set current buffer size: %d >= %d"
                % (len(telegram), self._buffer_size)
            )

        data_recived = bytearray()
        try:
            # send bytes to control
            self._tcpsock.send(bytes(telegram))
            if wait_for_response:
                data_recived = self._tcpsock.recv(self._buffer_size)
        except Exception:
            self._logger.error(
                "something went wrong while waiting for new data to arrive, buffer was set to %d",
                self._buffer_size,
            )
            raise

        if len(data_recived) > 0:
            self._logger.debug(
                "received block of data with length %d", len(data_recived))
            if len(data_recived) >= 8:
                # read 4 bytes for response length
                response_length = struct.unpack("!L", data_recived[0:4])[0]

                # read 4 bytes for response type
                self._last_lsv2_response = RSP(
                    data_recived[4:8].decode("utf-8", "ignore"))
            else:
                # response is less than 8 bytes long which is not enough space for package length and response message!
                raise Exception(
                    "response to short, less than 8 bytes: %s" % data_recived)
        else:
            response_length = 0
            self._last_lsv2_response = RSP.NONE

        if response_length > 0:
            response_content = bytearray(data_recived[8:])
            while len(response_content) < response_length:
                self._logger.debug(
                    "waiting for more data to arrive, %d bytes missing",
                    len(response_content) < response_length,
                )
                try:
                    response_content.extend(
                        self._tcpsock.recv(
                            response_length - len(data_recived[8:]))
                    )
                except Exception:
                    self._logger.error(
                        "something went wrong while waiting for more data to arrive. expected %d, recived %d, content so far: %s",
                        response_length,
                        len(data_recived),
                        data_recived
                    )
                    raise
        else:
            response_content = bytearray()

        if self._last_lsv2_response is RSP.T_ER:
            self._last_error = TransmissionError.from_ba(response_content)
        else:
            self._last_error = TransmissionError()

        return response_content
