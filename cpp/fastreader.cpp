/*
 * Copyright (C) 2024-2025 The Paparazzi Team
 *
 * This file is part of paparazzi.
 *
 * paparazzi is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2, or (at your option)
 * any later version.
 *
 * paparazzi is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with paparazzi; see the file COPYING.  If not, write to
 * the Free Software Foundation, 59 Temple Place - Suite 330,
 * Boston, MA 02111-1307, USA.
 */

#include <boost/iostreams/device/mapped_file.hpp> // for mmap
#include <boost/filesystem.hpp> // for is_empty
#include <boost/regex.hpp>
#include <boost/spirit/home/x3.hpp>
#include <boost/spirit/include/qi_lazy.hpp>
#include <algorithm>  // for std::find
#include <iostream>   // for std::cout
#include <cstring>
#include <tinyxml2.h>
#include <iostream>
#include <regex>

#include <pprzlink/MessageDictionary.h>
#include <pprzlink/MessageDefinition.h>
#include <pprzlink/Message.h>
#include <pprzlink/MessageFieldTypes.h>

using namespace boost::spirit::x3;

std::map<uint8_t, tinyxml2::XMLElement *> aircrafts;
void parse_airframe_list(tinyxml2::XMLElement *root)
{
  // char *ac_or_af = (root->FirstChildElement("conf")->FirstChildElement("aircraft"))? "aircraft" : "airframe";
  // auto aircraft = root->FirstChildElement("conf")->FirstChildElement(ac_or_af);
  // while (aircraft != nullptr) {
  //   auto className = aircraft->Attribute("name", nullptr);
  //   if (className == nullptr) {
  //     className = aircraft->Attribute("NAME", nullptr);
  //   }
  //   int classId = aircraft->IntAttribute("ac_id", -1);
  //   if (classId == -1) {
  //     classId = aircraft->IntAttribute("AC_ID", -1);
  //   }
  //   if (className == nullptr || classId == -1) {
  //     std::cout << "aircraft has no name or ac_id.";
  //   }
  //   std::cout << " - aircraft: " << className << " id: " << classId << "\n";
  //   aircrafts[classId] = aircraft;
  //   aircraft = aircraft->NextSiblingElement(ac_or_af);
  // }
}

//using List =  boost::mpl::list<double, std::string>;


/* Generate a pprzlink message */
pprzlink::Message get_msg(std::string name, pprzlink::MessageDictionary *dict,
                          auto values)
{
  pprzlink::MessageDefinition def = dict->getDefinition(name);
  pprzlink::Message msg(def);
  for (size_t i = 0; i < def.getNbFields(); i++) {
    auto field = def.getField(i);

    switch (field.getType().getBaseType()) {
      case pprzlink::BaseType::INT8:
      case pprzlink::BaseType::INT16:
      case pprzlink::BaseType::INT32:
      case pprzlink::BaseType::UINT8:
      case pprzlink::BaseType::UINT16:
      case pprzlink::BaseType::UINT32:
      case pprzlink::BaseType::FLOAT:
      case pprzlink::BaseType::DOUBLE:
        if (field.getType().isArray()) {
          std::vector<double> vals;
          for (auto val : values[i]) {
            vals.push_back(boost::get<double>(val));
          }
          msg.addField(field.getName(), vals);
        } else {
          msg.addField(field.getName(), boost::get<double>(values[i][0]));
        }
        break;
      case pprzlink::BaseType::STRING:
        msg.addField(field.getName(), boost::get<std::string>(values[i][0]));
        break;
      case pprzlink::BaseType::CHAR:
        if (field.getType().isArray()) {
          std::string vals;
          size_t end = i + 1;
          if (i + 1 == def.getNbFields()) {
            end = values.size();
          }

          for (size_t j = i; j < end; j++) {
            for (size_t k = 0; k < values[j].size(); k++) {
              auto val = boost::get<std::string>(values[j][k]);
              vals += val;
              vals += ",";
            }
            vals.resize(vals.length() - 1);
            vals += " ";
          }
          vals.resize(vals.length() - 1);

          if (vals[0] == '"') {
            vals = vals.substr(1, vals.size() - 2);
          }
          msg.addField(field.getName(), vals);

        } else {
          msg.addField(field.getName(), boost::get<std::string>(values[i][0]));
        }
        break;
      default:
        break;
    }
  }
  return msg;
}



int main(int argc, char *argv[])
{
  if (argc < 2) {
    std::cerr << "Usage: " << argv[0] << " <filename>\n";
    return 1;
  }

  // Check if the data file exists
  if (boost::filesystem::is_empty(argv[1])) {
    std::cerr << " - Empty DATA file\n";
    return 1;
  }
  std::cout << "LOG: " << argv[1] << "\n";

  // Replace and check if the log file exists
  std::string log_file = argv[1];
  log_file.replace(log_file.end() - 4, log_file.end(), "log");
  if (boost::filesystem::is_empty(log_file)) {
    std::cerr << " - Empty LOG file\n";
    return 1;
  }

  // Open the .log file in the xml parser
  tinyxml2::XMLDocument xml;
  xml.LoadFile(log_file.c_str());

  // Check if the root element is configuration
  tinyxml2::XMLElement *root = xml.RootElement();
  std::string rootElem(root->Value());
  if (rootElem != "configuration") {
    std::cerr << "Root element is not configuration in xml messages file (found " + rootElem + ").";
    return 1;
  }

  // Load message definitions from *.LOG / messages.xml
  pprzlink::MessageDictionary *dict = new pprzlink::MessageDictionary(root->FirstChildElement("protocol"));

  // Load all aircraft names
  parse_airframe_list(root);



  // GPS_INT message
  auto gps_int = [&](auto & ctx) {
    // auto timestamp = boost::fusion::at_c<0>(_attr(ctx));
    auto ac_id = uint8_t(boost::fusion::at_c<1>(_attr(ctx)));
    auto values = boost::fusion::at_c<2>(_attr(ctx));
    auto msg = get_msg("GPS_INT", dict, values);
    msg.setSenderId(ac_id);

    // std::cout << " - GPS_INT: " << timestamp << " " << msg.toString() << "\n";
  };

  // INFO_MSG message
  auto info_msg = [&](auto & ctx) {
    auto timestamp = boost::fusion::at_c<0>(_attr(ctx));
    auto ac_id = uint8_t(boost::fusion::at_c<1>(_attr(ctx)));
    auto values = boost::fusion::at_c<2>(_attr(ctx));
    auto msg = get_msg("INFO_MSG", dict, values);
    msg.setSenderId(ac_id);

    std::cout << " - INFO_MSG: " << timestamp << " " << msg.toString() << "\n";
  };

  // ROTORCRAFT_FP message
  auto rotorcraft_fp = [&](auto & ctx) {
    // auto timestamp = boost::fusion::at_c<0>(_attr(ctx));
    auto ac_id = uint8_t(boost::fusion::at_c<1>(_attr(ctx)));
    auto values = boost::fusion::at_c<2>(_attr(ctx));
    auto msg = get_msg("ROTORCRAFT_FP", dict, values);
    msg.setSenderId(ac_id);

    // std::cout << " - ROTORCRAFT_FP: " << timestamp << " " << msg.toString() << "\n";
  };

  // STAB_ATTITUDE message
  auto stab_attitude = [&](auto & ctx) {
    auto timestamp = boost::fusion::at_c<0>(_attr(ctx));
    auto ac_id = uint8_t(boost::fusion::at_c<1>(_attr(ctx)));
    auto values = boost::fusion::at_c<2>(_attr(ctx));
    auto msg = get_msg("STAB_ATTITUDE", dict, values);
    msg.setSenderId(ac_id);

    // std::cout << " - STAB_ATTITUDE: " << timestamp << " " << msg.toString() << "\n";
  };

  // AUTOPILOT_VERSION message
  auto autopilot_version = [&](auto & ctx) {
    auto timestamp = boost::fusion::at_c<0>(_attr(ctx));
    auto ac_id = uint8_t(boost::fusion::at_c<1>(_attr(ctx)));
    auto values = boost::fusion::at_c<2>(_attr(ctx));
    auto msg = get_msg("AUTOPILOT_VERSION", dict, values);
    msg.setSenderId(ac_id);

    std::cout << " - AUTOPILOT_VERSION: " << timestamp << " " << msg.toString() << "\n";
  };

  // Add the parser
  auto var_types = (double_ | lexeme[+~char_("\r\n")]);
  auto var_options = (var_types % ',');
  auto matcher = (float_ >> ' ' >> int_ >> ' ' >> "GPS_INT" >> ' ' >> (var_options % ' ') >> eol)[gps_int] \
                 | (float_ >> ' ' >> int_ >> ' ' >> "INFO_MSG" >> ' ' >> (var_options % ' ') >> eol)[info_msg] \
                  | (float_ >> ' ' >> int_ >> ' ' >> "ROTORCRAFT_FP" >> ' ' >> (var_options % ' ') >> eol)[rotorcraft_fp] \
                  | (float_ >> ' ' >> int_ >> ' ' >> "STAB_ATTITUDE" >> ' ' >> (var_options % ' ') >> eol)[stab_attitude] \
                  | (float_ >> ' ' >> int_ >> ' ' >> "AUTOPILOT_VERSION" >> ' ' >> (var_options % ' ') >> eol)[autopilot_version];
  auto res = matcher | ( * ~char_("\r\n") >> eol);

  // Parse the file
  boost::iostreams::mapped_file_source file(argv[1]);
  parse(file.begin(), file.end(), *(res));
}
