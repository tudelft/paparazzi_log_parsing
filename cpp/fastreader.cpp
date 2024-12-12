#include <boost/iostreams/device/mapped_file.hpp> // for mmap
#include <boost/filesystem.hpp> // for is_empty
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




void parse_airframe_list(tinyxml2::XMLElement *root) {
    auto aircraft = root->FirstChildElement("conf")->FirstChildElement("aircraft");
    while (aircraft != nullptr)
    {
        auto className = aircraft->Attribute("name", nullptr);
        if (className == nullptr)
        {
            className = aircraft->Attribute("NAME", nullptr);
        }
        int classId = aircraft->IntAttribute("ac_id", -1);
        if (classId == -1)
        {
            classId = aircraft->IntAttribute("AC_ID", -1);
        }
        if (className == nullptr || classId == -1)
        {
            std::cout << "aircraft has no name or ac_id.";
        }
        // std::cout << " - aircraft: " << className << " id: " << classId << "\n";
        aircraft = aircraft->NextSiblingElement("aircraft");
    }
}


void parse_message(std::string fieldsStr, pprzlink::MessageDictionary *dict)
{
    // std::cout << " - message: " << fieldsStr << "\n";

    std::regex fieldRegex("([^ ]+|\"[^\"]+\")");

    std::smatch results;
    std::vector<std::string> fields;
    while (std::regex_search(fieldsStr, results, fieldRegex))
    {
        fields.push_back(results.str());
        // std::cout << " - field: " << results.str() << "\n";
        fieldsStr = results.suffix();
    }

    pprzlink::MessageDefinition def = dict->getDefinition(fields[2]);
    pprzlink::Message msg(def);

    int argc = fields.size();
    if (def.getNbFields() != (size_t)(argc - 3) )
    {
      std::stringstream sstr;
      sstr << fields[2] << " message with wrong number of fields (expected " << def.getNbFields() << " / got " << argc - 3
           << ")";
      std::cout << (sstr.str());
    }

    for (int i = 3; i < argc; ++i)
    {
      const auto& field = def.getField(i - 3);
      // Need deserializing string to build FieldValue

      // For char arrays and strings remove possible quotes
      if ((field.getType().getBaseType()==pprzlink::BaseType::STRING || (field.getType().getBaseType()==pprzlink::BaseType::CHAR && field.getType().isArray())) && fields[i][0]=='"')
      {
        std::string str(fields[i]);
        //std::cout << str.substr(1,str.size()-2) << std::endl;
        msg.addField(field.getName(),str.substr(1,str.size()-2));
      }
      else
      {
        std::stringstream sstr(fields[i]);
        if (field.getType().isArray())
        {
          switch (field.getType().getBaseType())
          {
            case pprzlink::BaseType::NOT_A_TYPE:
              throw std::logic_error("NOT_A_TYPE for field " + field.getName() + " in message " + fields[2]);
              break;
            case pprzlink::BaseType::CHAR:
              std::cout << "Wrong field format for a char[] "+std::string(fields[i]);
              break;
            case pprzlink::BaseType::INT8:
            case pprzlink::BaseType::INT16:
            case pprzlink::BaseType::INT32:
            case pprzlink::BaseType::UINT8:
            case pprzlink::BaseType::UINT16:
            case pprzlink::BaseType::UINT32:
            case pprzlink::BaseType::FLOAT:
            case pprzlink::BaseType::DOUBLE:
            {
              // Parse all numbers as a double
              std::vector<double> values;
              while (!sstr.eof())
              {
                double val;
                char c;
                sstr >> val >> c;
                if (c!=',')
                {
                  std::cout << "Wrong format for array "+std::string(fields[i]);
                }
                values.push_back(val);
              }
              msg.addField(field.getName(), values); // The value will be statically cast to the right type
            }
              break;
            case pprzlink::BaseType::STRING:
              msg.addField(field.getName(), fields[i]);
              break;
          }
        }
        else
        {
          switch (field.getType().getBaseType())
          {
            case pprzlink::BaseType::NOT_A_TYPE:
              throw std::logic_error("NOT_A_TYPE for field " + field.getName() + " in message " + fields[2]);
              break;
            case pprzlink::BaseType::CHAR:
            {
              char val;
              sstr >> val;
              msg.addField(field.getName(), val);
            }
              break;
            case pprzlink::BaseType::INT8:
            case pprzlink::BaseType::INT16:
            case pprzlink::BaseType::INT32:
            case pprzlink::BaseType::UINT8:
            case pprzlink::BaseType::UINT16:
            case pprzlink::BaseType::UINT32:
            case pprzlink::BaseType::FLOAT:
            case pprzlink::BaseType::DOUBLE:
            {
              // Parse all numbers as a double
              double val;
              sstr >> val;
              msg.addField(field.getName(), val); // The value will be statically cast to the right type
            }
              break;
            case pprzlink::BaseType::STRING:
              msg.addField(field.getName(), fields[i]);
              break;
          }
        }
      }
    }


}






int main(int argc, char* argv[])
{
    if (argc < 2)
    {
        std::cout << "Usage: " << argv[0] << " <filename>\n";
        return 1;
    }


    if (boost::filesystem::is_empty(argv[1])) {
        std::cout << " - Empty DATA file\n";
        return 0;
    }

    std::cout << "LOG: " << argv[1] << "\n";


    // boost::iostreams::mapped_file mmap(argv[1], boost::iostreams::mapped_file::readonly);
    // auto f = mmap.const_data();
    // auto l = f + mmap.size();


    // replace the .data in argv[1] with .log
    std::string log_file = argv[1];
    log_file.replace(log_file.end()-4, log_file.end(), "log");

    std::cout << " - log_file = " << log_file << "\n";


    if (boost::filesystem::is_empty(log_file)) {
        std::cout << " - Empty LOG file\n";
        return 0;
    }


    tinyxml2::XMLDocument xml;
    xml.LoadFile(log_file.c_str());
    // Get a link to the root element
    tinyxml2::XMLElement *root = xml.RootElement();
    std::string rootElem(root->Value());
    if(rootElem!="configuration")
    {
        std::cout << "Root element is not configuration in xml messages file (found "+rootElem+").";
        return 0;
    }

    // Load message definitions from *.LOG / messages.xml
    pprzlink::MessageDictionary *dict = new pprzlink::MessageDictionary(root->FirstChildElement("protocol"));
//    pprzlink::MessageDictionary *dict = new pprzlink::MessageDictionary("./pprzlink/message_definitions/v1.0/messages.xml");


    // Load all aircraft names
    parse_airframe_list(root);


    const char* msg = "17.316 44 ROTORCRAFT_FP 0 0 107 0 0 51597 71 85 16 122 -87 102 16 0 0";
    std::string message(msg);
    parse_message(message, dict);



    // const char *PIC = "[PFC] pic:";

    // // Find the first occurrence of the string "[PFC] pic:"
    // auto it = std::search(f, l, PIC, PIC + strlen(PIC));
    // if (it != l) {
    //     // Print the line where the string was found
    //     auto start = it;
    //     while (start != f && *start != '\n') start--;
    //     start++;
    //     auto end = it;
    //     while (end != l && *end != '\n') end++;
    //     std::cout << " - Found: " << std::string(start, end) << "\n";
    //     return 0;
    // }


    // // Find the number of lines in the file
    uintmax_t m_numLines = 0;   
    // while (f && f!=l) {
    //     if ((f = static_cast<const char*>(memchr(f, '\n', l-f)))) {

    //         // Parse line
    //         // const char* message = (static_cast<const char*>(f));
    //         // parse_message(message, dict);

    //         m_numLines++, f++;
    //     }
    // }


    boost::filesystem::ifstream fileHandler(argv[1]);
    std::string line;
    while (getline(fileHandler, line)) {
        parse_message(line, dict);
    }



    std::cout << " - m_numLines = " << m_numLines << "\n";
//    std::cout << " - mmap.size() = " << mmap.size() << "\n";
}

