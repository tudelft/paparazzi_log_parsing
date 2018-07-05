function [gyro, accel, mag, baro] = read_imu_log(filename)

gyro=[];
accel=[];
mag=[];
baro=[];

fid = fopen(filename, 'r');

while 1
  tline = fgetl(fid);
  if ~ischar(tline),   break,   end
  [A, count] = sscanf(tline, '%f %d IMU_MAG_RAW %d %d %d');
  if (count == 5), mag = [mag A(3:5)];, end;
  [A, count] = sscanf(tline, '%f %d IMU_ACCEL_RAW %d %d %d');
  if (count == 5), accel = [accel A(3:5)];, end;
  [A, count] = sscanf(tline, '%f %d IMU_GYRO_RAW %d %d %d');
  if (count == 5), gyro = [gyro A(3:5)];, end;
  [A, count] = sscanf(tline, '%f %d BARO_RAW %f %f');
  if (count == 4), baro = [baro A(3:4)];, end;
end

fclose(fid)