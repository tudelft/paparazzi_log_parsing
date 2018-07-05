function [rotor, gps, temp, mot, fbw, energy, status, air] = read_rotorcraft_log(filename)

cnt = 0;

rotor.id=[];
rotor.t=[];
rotor.x=[];
rotor.x_sp=[];
rotor.v=[];
rotor.phi=[];
rotor.theta=[];
rotor.psi=[];
rotor.psi_sp=[];
rotor.thrust=[];
rotor.flight_time=[];
rotor.cnt=[];

status.t=[];
status.gps=[];
status.mode=[];
status.kill=[];
status.rc=[];
status.inflight=[];
status.cpu_time=[];
status.vbat=[];

gps.t=[];
gps.ecef=[];
gps.lat=[];
gps.lon=[];
gps.alt=[];;
gps.hmsl=[];
gps.vecef=[];
gps.pacc=[];
gps.tow=[];
gps.cnt=[];

temp.t=[];
temp.mot=[];
temp.bat=[];

mot.t=[];
mot.rpm=[];
mot.current=[];

fid = fopen(filename, 'r');

fbw.t = [];
fbw.mode = [];
fbw.rc = [];
fbw.volt = [];

energy.t = [];
energy.vbat = [];
energy.amp = [];

air.t = [];
air.pressure = [];
air.speed = [];

while 1
  tline = fgetl(fid);
  cnt=cnt+1;
  if ~ischar(tline),   break,   end
  [A, count] = sscanf(tline, '%f %d ROTORCRAFT_FP %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d');
  if (count == 17)
    rotor.t=[rotor.t; A(1)];
    rotor.x=[rotor.x; A(3:5)'.*0.0039063];
    rotor.v=[rotor.v; A(6:8)'.*0.0000019];
    rotor.phi=[rotor.phi; A(9).*0.0139882];
    rotor.theta=[rotor.theta; A(10).*0.0139882];
    rotor.psi=[rotor.psi; A(11).*0.0139882];
    rotor.x_sp=[rotor.x_sp; A(12:14)'.*0.0039063];
    rotor.psi_sp=[rotor.psi_sp; A(15).*0.0139882];
    rotor.thrust=[rotor.thrust; A(16)];
    rotor.flight_time=[rotor.flight_time;A(17)];
    rotor.cnt=[rotor.cnt;cnt];
  end;
  [A, count] = sscanf(tline, '%f %d AIR_DATA %f %f %f %f %f %f %f');
  if (count == 9)
    air.t=[air.t; A(1)];
    air.pressure=[air.pressure; A(3)];
    air.speed=[air.speed; A(8)];
  end;
  [A, count] = sscanf(tline, '%f %d ROTORCRAFT_STATUS %d %d %d %d %d %d %d %d %d %d %d %d');
  if (count == 15)
    status.t=[status.t; A(1)];
    status.gps=[status.gps; A(7)];
    status.mode=[status.mode; A(8)];
    status.kill=[status.kill; A(10)];
    status.rc=[status.rc; A(5)];
    status.inflight=[status.inflight; A(9)];
    status.vbat=[status.vbat; A(14)/10];
    status.cpu_time=[status.cpu_time; A(15)];
  end;
  [A, count] = sscanf(tline, '%f %d GPS_INT %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d');
  if (count == 19)
    gps.t=[gps.t; A(1)];
    gps.ecef=[gps.ecef;A(3:5)'./100];
    gps.lat=[gps.lat;A(6)./1e7];
    gps.lon=[gps.lon;A(7)./1e7];
    gps.alt=[gps.alt;A(8)./1000];
    gps.hmsl=[gps.hmsl;A(9)./1000];
    gps.vecef=[gps.vecef;A(10:12)'./100];
    gps.pacc=[gps.pacc;A(13)./100];
    gps.tow=[gps.tow;A(15)];
    gps.cnt=[gps.cnt;cnt];
  end
  [A, count] = sscanf(tline, '%f %d TEMP_ADC %f %f %f');
  if (count == 5)
    temp.t=[temp.t; A(1)];
    temp.mot=[temp.mot;A(3)];
    temp.bat=[temp.bat;A(4)];
  end
  [A, count] = sscanf(tline, '%f %d MOTOR %d %d');
  if (count == 4)
    mot.t=[mot.t; A(1)];
    mot.rpm=[mot.rpm;A(3)];
    mot.current=[mot.current;A(4)];
  end
  [A, count] = sscanf(tline, '%f %d ENERGY %f %f %d %f');
  if (count == 6)
    energy.t=[energy.t; A(1)];
    energy.vbat=[energy.vbat;A(3)];
    energy.amp=[energy.amp;A(4)];
  end
  [A, count] = sscanf(tline, '%f %d FBW_STATUS %d %d %d %d %d');
  if (count == 7)
    fbw.t=[fbw.t; A(1)];
    fbw.rc=[fbw.rc;A(4)];
    fbw.mode=[fbw.mode;A(5)];
    fbw.volt=[fbw.volt;A(6)/10];
  end
  
end

fclose(fid);