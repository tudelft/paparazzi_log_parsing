function takeoff_distance(ac_data, motors_on)

    % Calculate the takeoff distance for all rolling takeoffs 

    takeoff_threshold = 0.1; % what LIDAR distance change constitutes takeoff

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

        disp("time: " + start_time + " dist: " + distance_traveled_takeoff + " end_time " + end_time)

    end

end