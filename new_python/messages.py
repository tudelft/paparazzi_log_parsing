import xml.etree.ElementTree as ET


def messages(filename: str) -> dict:
    # Parse XML file
    tree = ET.parse(filename)
    msg_classes = tree.findall('msg_class')

    msgs = {}

    # Go through the message classes
    for mc in msg_classes:
        mc_dict = parse_message_class(mc)
        msgs[mc_dict["name"]] = mc_dict["msgs"]
    return msgs


# Parse a msg_class node
def parse_message_class(mc: ET.Element) -> dict:
    c = {}
    if mc.get('name') is not None:
        c["name"] = mc.get('name')
    elif mc.get('NAME') is not None:
        c["name"] = mc.get('NAME')
    else:
        raise ValueError('Messages xml contains an invalid msg_class without a name')

    # Go through the messages and parse them
    c["msgs"] = {}
    mc_messages = mc.findall('message')
    for message in mc_messages:
        m = parse_message(message)
        c["msgs"][m["name"]] = m
    return c


# Parse a message node
def parse_message(msg: ET.Element) -> dict:
    m = {"fields": {}}
    if msg.get('name') is not None:
        m["name"] = msg.get('name')
    elif msg.get('NAME') is not None:
        m["name"] = msg.get('NAME')
    else:
        raise ValueError('Messages xml contains an invalid message without a name')

    # Go through the message fields
    m["field_names"] = []
    msg_fields = msg.findall('field')
    for field in msg_fields:
        f = parse_field(field)
        m["field_names"].append(f["name"])
        m["fields"][f["name"]] = f
    return m


# Parse a field node from a message
def parse_field(field: ET.Element) -> dict:
    f = {"name": field.get('name')}

    # Parse field attributes
    if f["name"] is None:
        f["name"] = field.get('NAME')

    f["type"] = field.get('type')
    if f["type"] is None:
        f["type"] = field.get('TYPE')

    f["unit"] = field.get('unit')
    if f["unit"] is None:
        f["unit"] = field.get('UNIT')

    f["values"] = field.get('values')
    if f["values"] is None:
        f["values"] = field.get('VALUES')

    f["alt_unit"] = field.get('alt_unit')
    if f["alt_unit"] is None:
        f["alt_unit"] = field.get('ALT_UNIT')

    f["alt_unit_coef"] = field.get('alt_unit_coef')
    if f["alt_unit_coef"] is None:
        f["alt_unit_coef"] = field.get('ALT_UNIT_COEF')
    if f["alt_unit_coef"] is None:
        f["alt_unit_coef"] = 1
    else:
        f["alt_unit_coef"] = float(f["alt_unit_coef"])
    return f
