function [ id ] = plot_rotor( log, nr )
%UNTITLED Summary of this function goes here
%   Detailed explanation goes here

id = 1;

c_m_to_ft = 3.28084;
c_ms_to_kts = 1.94384;
c_km_to_kt = 1/1.852;





range=log.gps.lat>50 & log.gps.lat<53 & log.gps.lon>0 & log.gps.lon<6;
if (size(log.good,2) >= 2)
    range=log.gps.t>log.good(1) & log.gps.t<log.good(2);
    disp('Reduced dataset')
end

g = [log.gps.lat(range) log.gps.lon(range) log.gps.hmsl(range)];

kml(g, ['gpstrack' num2str(nr, '%03d') ]);


figure

if (size(log.r.t,1) == 0)
    disp('Not a ROTORCRAFT logfile. Maybe a calibration file?');
    [gyro, accel, mag, baro] = read_imu_log(log.file);
    mag = mag';
    figure
    plot3(mag(:,1),mag(:,2), mag(:,3));
    axis equal
    grid on
    title(log.file)

    set(gcf,'PaperSize', [30 25],'PaperPosition',[0 0 30 25])
    print(gcf,['log' num2str(nr, '%03d') '.pdf'],'-dpdf')
    
    return
end


log.valid = 1;

if (size(log.good) ~= [1 2] )
    log.good = [1 10000000000000000];
    log.valid = 0;
end


nextline = -0.1;
line = 1.0 + nextline;

subplot(3,3,7)
log.nr_of_flights = 1;
if (size(log.flight,2) == 2)
    fl = log.flight(:,2) - log.flight(:,1);
    disp('Logfile with: ' );
    log.nr_of_flights = size(log.flight,1);
    
    for i=1:log.nr_of_flights
        str = (['Flight ' num2str(i) ': Time: ' num2str(fl(i)) ' s'])
        disp(str)
        text(0.1,line,str)
        line = line + nextline;
    end
    str = (['Total: ' num2str(sum(fl)) ' sec']);
    disp(str)
    text(0.1,line,str)
    line = line + nextline;
    str = (['Total: ' num2str(sum(fl)/60) ' min']);
    %disp(str)
    text(0.1,line,str)
else
    log.flight = [1 10000000000000000];
    log.valid = 0;
end


%vx = log.r.v(s:end,1);
%vy = log.r.v(s:end,2);
%t = log.r.t(s:end);

% figure
% %plot(t,vx)
% hold on
% %plot(t,vy)
% grid on
% xlabel('time [s]');
% ylabel('speed [km/h]');
% grid on
% plot(t,sqrt(vx.*vx + vy.*vy).*3.6)
% grid on
% plot(t(1:end-1:end),[98.5; 98.5],'g')
% plot(t(1:end-1:end),[134.7; 134.7],'r')
% plot(t(1:end-1:end),[62.25; 62.25],'r')
% legend('Measured', 'Airspeed', 'Headwind', 'Tailwind')

h = log.r.x(:,3);
hs = log.r.x_sp(:,3);


subplot(3,3,4)
range=log.r.t>log.good(1) & log.r.t<log.good(2) & log.r.x(:,1) < 10000 & log.r.x(:,2) < 10000  & log.r.x(:,1) > -10000 & log.r.x(:,2) > -10000;
plot3(log.r.x(range,1),log.r.x(range,2),log.r.x(range,3))
axis equal
grid on


dx = diff (log.r.x(range,:),1,1);
dx = dx.^2;
d = sqrt( dx(:,1) + dx(:,2) ); %+ dx(:,3) );
distance = sum(d);

subplot(3,3,7)
if isfield(log, 'comment')
    text(0.1,line + nextline*3,log.comment)
end
text(0.1,line + nextline*4,[ 'Nautical Miles ' num2str(round(distance/1000*c_km_to_kt,2))] )

subplot(3,3,1)
range=log.r.t>log.good(1) & log.r.t<log.good(2);
plot(log.r.t(range),h(range).*c_m_to_ft)
grid on
hold on
plot(log.r.t(range),hs(range).*c_m_to_ft,'r');
%plot(log.gps.t,log.gps.hmsl+6);
legend('Baro', 'Command');
xlabel('time [s]')
ylabel('altitude [ft]')
%subplot(3,3,2)
%plot(log.r.t,log.r.psi)



subplot(3,3,2)
range=log.temp.t>log.good(1) & log.temp.t<log.good(2);
plot(log.temp.t(range),log.temp.bat(range))
hold on
plot(log.temp.t(range),log.temp.mot(range))
for i=1:log.nr_of_flights
    range=log.temp.t>log.flight(i,1) & log.temp.t<log.flight(i,2);
    %plot(log.temp.t(range),log.temp.mot(range),'LineWidth', 3);
end
grid on;
legend('bat','mot')
xlabel('time [s]')
ylabel('temperature [deg]')
title(log.file)

subplot(3,3,3)
range=log.mot.t>log.good(1) & log.mot.t<log.good(2);
plot(log.mot.t(range),log.mot.rpm(range))
grid on;
xlabel('time [s]')
ylabel('rotor [rpm]')


subplot(3,3,5)
range=log.energy.t>log.good(1) & log.energy.t<log.good(2);
plot(log.energy.t(range),log.energy.amp(range));%.*5.0./3.3+.1)
grid on;
xlabel('time [s]')
ylabel('current [A]')
if isfield(log, 'comment')
    title(log.comment)
end


subplot(3,3,6)
range=log.status.t>log.good(1) & log.status.t<log.good(2);
plot(log.status.t(range),log.status.vbat(range),'b');

range=log.status.t>log.good(1) & log.status.t<log.good(2) & log.status.mode==13;
plot(log.status.t(range),log.status.vbat(range),'g', 'LineWidth', 3);
hold on;
range=log.fbw.t>log.good(1) & log.fbw.t<log.good(2);
plot(log.fbw.t(range),log.fbw.volt(range))
grid on;
xlabel('time [s]')
ylabel('vbat [V]')
range=log.fbw.t>log.good(1) & log.fbw.t<log.good(2) & log.fbw.mode==1;
plot(log.fbw.t(range),log.fbw.volt(range),'r', 'LineWidth', 3);

%%

subplot(3,3,9)
hold off
plot(log.air.t,log.air.speed.*c_ms_to_kts);
grid on;
xlabel('time [s]')
ylabel('airspeed [kt]')

%%

subplot(3,3,8)
hold off

% airspeed + heading
airinterp = interp1(log.air.t,log.air.speed,log.r.t);

vx = log.r.v(:,1);
vy = log.r.v(:,2);

ax = airinterp .* sind(log.r.psi);
ay = airinterp .* cosd(log.r.psi);

wxv = (ax-log.r.v(:,1));
wyv = (ay-log.r.v(:,2));

wx = mean(wxv(~isnan(wxv)))
wy = mean(wyv(~isnan(wyv)))

wind = sqrt(wx^2 + wy^2)

a=[vx vy ones(size(vx))]\[-(vx.^2+vy.^2)];
xc = -.5*a(1)
yc = -.5*a(2)
R  =  sqrt((a(1)^2+a(2)^2)/4-a(3))

plot(vx,vy)
grid on;
axis equal;
hold on;


plot(ax-wx,ay-wy,'r')
legend('Groundspeed', 'Airspeed+wind')

deg = 0:360;
plot( xc + cosd(deg) .* R, yc + sind(deg) .* R , 'g');

text(0,0,['wind = ' num2str(wind) ' m/s'])

plot(wx,wy)

%%

set(gcf,'PaperSize', [30 25],'PaperPosition',[0 0 30 25])
print(gcf,['log' num2str(nr, '%03d') '.pdf'],'-dpdf')


end

