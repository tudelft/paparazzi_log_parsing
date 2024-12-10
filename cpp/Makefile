



all:
	mkdir -p build
	cd build && cmake ../
	make -C ./build
	
clean:
	rm -rf ./build
	rm -rf ./build_tinyxml2
	rm -rf ./build_pprzlink


lib:
	cd tinyxml2 && make
	mkdir -p build_tinyxml2 && cd build_tinyxml2 && cmake ../tinyxml2 && make && sudo make install
	mkdir -p build_pprzlink && cd build_pprzlink && cmake ../pprzlink/lib/v2.0/C++ && make && sudo make install
