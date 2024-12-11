


all:
	make -C ./cpp lib
	make -C ./cpp
	python3 ./create_logbook.py
