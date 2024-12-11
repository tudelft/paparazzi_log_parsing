#include <boost/iostreams/device/mapped_file.hpp> // for mmap
#include <boost/filesystem.hpp> // for is_empty
#include <algorithm>  // for std::find
#include <iostream>   // for std::cout
#include <cstring>
#include <tinyxml2.h>

#include <pprzlink/MessageDictionary.h>

bool parse_line(std::string line) {
    return true;
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


    boost::iostreams::mapped_file mmap(argv[1], boost::iostreams::mapped_file::readonly);
    auto f = mmap.const_data();
    auto l = f + mmap.size();


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
        std::cout << " - aircraft: " << className << " id: " << classId << "\n";
        aircraft = aircraft->NextSiblingElement("aircraft");
    }

    const char *PIC = "[PFC] pic:";

    // Find the first occurrence of the string "[PFC] pic:"
    auto it = std::search(f, l, PIC, PIC + strlen(PIC));
    if (it != l) {
        // Print the line where the string was found
        auto start = it;
        while (start != f && *start != '\n') start--;
        start++;
        auto end = it;
        while (end != l && *end != '\n') end++;
        std::cout << " - Found: " << std::string(start, end) << "\n";
        return 0;
    }

    // Find the number of lines in the file
    uintmax_t m_numLines = 0;
    while (f && f!=l)
        if ((f = static_cast<const char*>(memchr(f, '\n', l-f))))
            m_numLines++, f++;



    std::cout << " - m_numLines = " << m_numLines << "\n";
//    std::cout << " - mmap.size() = " << mmap.size() << "\n";
}

