function [  ] = kml( g, file )
%UNTITLED Summary of this function goes here
%   Detailed explanation goes here


%load g
len = size(g,1)

filename = [file '.kml'];

fileID = fopen(filename,'w')

fprintf(fileID,'<kml xmlns="http://earth.google.com/kml/2.1">\n  <Document>\n')
fprintf(fileID,'    <name>%s</name>\n',file)
fprintf(fileID,'    <StyleMap id="msn_ylw-pushpin_copy1">\n      <Pair>\n        <key>normal</key>\n');
fprintf(fileID,'        <styleUrl>#sn_ylw-pushpin_copy1</styleUrl>\n      </Pair>\n      <Pair>\n        <key>highlight</key>\n        <styleUrl>#sh_ylw-pushpin_copy1</styleUrl>\n      </Pair>\n    </StyleMap>');
fprintf(fileID,'    <Style id="sn_ylw-pushpin_copy1">\n      <IconStyle>\n        <scale>1.1</scale>\n        <Icon>\n          <href>http://maps.google.com/mapfiles/kml/pushpin/ylw-pushpin.png</href>\n        </Icon>\n        <hotSpot x="20" y="2" xunits="pixels" yunits="pixels"/>\n      </IconStyle>\n      <LineStyle>\n        <color>ff0000ff</color>\n        <width>2</width>')
fprintf(fileID,'      </LineStyle>\n    </Style>\n    <Style id="sh_ylw-pushpin_copy1">\n      <IconStyle>\n        <scale>1.3</scale>\n        <Icon>\n          <href>http://maps.google.com/mapfiles/kml/pushpin/ylw-pushpin.png</href>\n        </Icon>\n        <hotSpot x="20" y="2" xunits="pixels" yunits="pixels"/>\n      </IconStyle>\n      <LineStyle>\n        <color>ff0000ff</color>\n        <width>2</width>\n      </LineStyle>\n    </Style>\n');
fprintf(fileID,'    <Placemark>\n')
fprintf(fileID,'      <name>%s</name>\n', 'GPS-Track')
fprintf(fileID,'      <styleUrl>#msn_ylw-pushpin_copy1</styleUrl>\n      <LineString>\n        <tessellate>1</tessellate>\n        <altitudeMode>absolute</altitudeMode>\n        <coordinates>')

for i=1:len
   fprintf(fileID,'%f,%f,%f\n',g(i,2),g(i,1),g(i,3));
end

fprintf(fileID,'</coordinates>\n      </LineString>\n    </Placemark>\n');
fprintf(fileID,'  </Document>\n</kml>\n');


fclose(fileID)


end