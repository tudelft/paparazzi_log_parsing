#include <boost/iostreams/device/mapped_file.hpp> // for mmap
#include <boost/filesystem.hpp> // for is_empty
#include <algorithm>  // for std::find
#include <iostream>   // for std::cout
#include <cstring>

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
//        std::cout << " - Empty file\n";
        return 0;
    }

    std::cout << "LOG: " << argv[1] << "\n";


    boost::iostreams::mapped_file mmap(argv[1], boost::iostreams::mapped_file::readonly);
    auto f = mmap.const_data();
    auto l = f + mmap.size();



    pprzlink::MessageDictionary *dict = new pprzlink::MessageDictionary("./pprzlink/message_definitions/v1.0/messages.xml");


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

