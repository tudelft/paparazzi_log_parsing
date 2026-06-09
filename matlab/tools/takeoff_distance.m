function takeoff_distance(ac_data, motors_on, takeoff_threshold)

    disp("Evaluating each takeoff in the flight log")

    % Calculate the takeoff distance for all rolling takeoffs 

    % if takeoff_threshold parameter is not provided, use default
    if nargin < 3
        takeoff_threshold = 0.1; % Default threshold value
    end

    long_arming = diff(motors_on) > 30;
    long_arming = long_arming(1:2:end);

    arm_times = motors_on(1:2:end);
    arm_times = arm_times(long_arming);

    copy_lidar_dist = ac_data.LIDAR.distance;

    % Look at all the odd values of motors_on, the other ones are
    % motors_off
    for start_time=(arm_times.')
        lidar_start_id = find(ac_data.LIDAR.timestamp >= start_time, 1, "first");
        initial_dist = ac_data.LIDAR.distance(find(ac_data.LIDAR.timestamp>=start_time,1,"first"));
        copy_lidar_dist(1:lidar_start_id) = initial_dist;
        
        end_time = ac_data.LIDAR.timestamp(find(copy_lidar_dist >= initial_dist+takeoff_threshold, 1, "first"));
        if(isempty(end_time))
            end_time = start_time;
        end
        
        id_start = find(ac_data.ROTORCRAFT_FP.timestamp >= start_time, 1, "first");
        id_end = find(ac_data.ROTORCRAFT_FP.timestamp >= end_time, 1, "first");
        
        n = ac_data.ROTORCRAFT_FP.north_alt(id_start) - ac_data.ROTORCRAFT_FP.north_alt(id_end);
        e = ac_data.ROTORCRAFT_FP.east_alt(id_start) - ac_data.ROTORCRAFT_FP.east_alt(id_end);
        
        distance_traveled_takeoff = sqrt(n^2 + e^2);

        % Calculate mean power during takeoff
        id_start_energy = find(ac_data.ENERGY.timestamp >= start_time, 1, "first");
        id_end_energy = find(ac_data.ENERGY.timestamp >= end_time, 1, "first");
        power_takeoff = ac_data.ENERGY.power(id_end_energy);

        airspeed_takeoff = ac_data.AIR_DATA.airspeed(find(ac_data.AIR_DATA.timestamp >= end_time, 1, "first"));

        % Display takeoff distance with one decimal
        disp("time: " + round(start_time,2) + " dist: " + round(distance_traveled_takeoff, 1) + " [m] end_time " + round(end_time,2) + " pitch angle: " + round(ac_data.ROTORCRAFT_FP.theta_alt(id_start),1) + " [deg] takeoff throttle " + round(ac_data.ROTORCRAFT_FP.thrust(id_end)*100/9600) + " % power at TO: " + round(power_takeoff) + " W measured airspeed TO: " + round(airspeed_takeoff,1))

    end

    disp(" ");

end