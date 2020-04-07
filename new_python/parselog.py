import os
import re

import numpy as np

from messages import messages, AttrDict


def parselog(filename: str, msg_xml: str = None) -> AttrDict:
    """
    Parse a data log extracting the fields based on a message.xml

    :param filename: path to the data log file name
    :param msg_xml: path to the message.xml file
    :return: dictionary containing the log data
    """

    f_suffix = filename.rsplit(".", maxsplit=1)
    if f_suffix[-1] != "data":
        raise ValueError("File type must be .data to be able to be parsed")

    # If message.xml is not specified try to find it from PAPARAZZI_HOME
    if msg_xml is None:
        msg_xml = os.getenv("PAPARAZZI_HOME") + "/var/messages.xml"

    msgs = messages(msg_xml)

    # Extract data into timestamp, AC_ID, msg name, and msg contents
    # Depending on the message the number of fields will be different, thus impossible to immediately turn into ndarray
    data = []
    with open(filename) as f:
        for line in f:
            data.append(line.rstrip().split(maxsplit=3))

    data = np.array(data)

    timestamp = np.asarray(data[:, 0], dtype=float)
    aircraft_id = np.asarray(data[:, 1], dtype=int)
    msg_name = np.asarray(data[:, 2], dtype=str)
    msg_content = np.asarray(data[:, 3], dtype=str)

    unique_ac = np.unique(aircraft_id)
    n_ac = unique_ac.size
    unique_msg = np.unique(msg_name)

    # Extract the data for all unique aircraft separately
    log_data = AttrDict()
    log_data.msgs = msgs
    log_data.aircrafts = [AttrDict()] * n_ac
    for i in range(n_ac):
        # Filter only data relevant to the aircraft in question
        id_mask = aircraft_id == unique_ac[i]

        log_data.aircrafts[i].AC_ID = unique_ac[i]
        log_data.aircrafts[i].data = parse_aircraft_data(msgs, unique_msg, timestamp[id_mask], msg_name[id_mask],
                                                         msg_content[id_mask])

        if "AUTOPILOT_VERSION" in log_data.aircrafts[i].data.keys():
            log_data.aircrafts[i].version = log_data.aircrafts[i].data.AUTOPILOTVERSION.version[0]
            log_data.aircrafts[i].version_desc = log_data.aircrafts[i].data.AUTOPILOTVERSION.desc[0]

    return log_data


def parse_aircraft_data(msgs: AttrDict, uniqueMsg: np.ndarray, timestamp: np.ndarray, msgName: np.ndarray,
                        msgContent: np.ndarray) -> AttrDict:
    ac_data = AttrDict()
    msg_info = None
    for msg in uniqueMsg:
        # Check the message class of the message
        ac_data[msg] = AttrDict()
        if msg in msgs.telemetry.keys():
            msg_info = msgs.telemetry[msg]
        elif msg in msgs.ground.keys():
            msg_info = msgs.ground[msg]
        elif msg in msgs.datalink.keys():
            msg_info = msgs.datalink[msg]
        elif msg in msgs.alert.keys():
            msg_info = msgs.alert[msg]

        msg_fields = msg_info.field_names
        msg_mask = msgName == msg  # Filter the message in question

        ac_data[msg].timestamp = timestamp[msg_mask]
        n_fields = len(msg_fields)

        # Only parse content if needed
        if n_fields > 0:
            content_list = [re.split(',| |, ', x) for x in msgContent[msg_mask]]
            content = np.asarray(content_list, dtype=float)

            for i in range(n_fields):
                field_name = msg_fields[i]
                values = content.T[i]
                ac_data[msg][field_name] = values

                # Parse alternate unit
                field_info = msg_info.fields[field_name]
                if field_info.alt_unit_coef != 1:
                    ac_data[msg][field_name + "_alt"] = values * field_info.alt_unit_coef
            else:
                # If there isn't a field type per content column, dump all remaining message content
                # into the last field. This usually occurs for array field type, e.g. int16[] (ACTUATORS)
                if i != content.shape[1]:
                    values = content[:, i:]
                    ac_data[msg][field_name] = values

                    if field_info.alt_unit_coef != 1:
                        ac_data[msg][field_name + "_alt"] = values * field_info.alt_unit_coef
    return ac_data


if __name__ == "__main__":
    pass
