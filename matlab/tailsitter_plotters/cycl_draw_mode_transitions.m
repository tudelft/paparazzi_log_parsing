%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
% chat gpt generated code use/modify with caution! %
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

function cycl_draw_mode_transitions(mode_values, mode_timestamps, ax_array)
    
    % Get the y-limits for each axis in ax_array
    y_limits = cell(1, length(ax_array));
    for i = 1:length(ax_array)
        y_limits{i} = ylim(ax_array{i});
    end

    % According to the transmitter the middle value below may be different.
    % Eg trasnmitter range 999 to 2000, middle value 1503 --> 75
    % Transition does NOT occur exactly at the lines! Check code!
    mode_colors = containers.Map({-9600, 75, 9600}, {[1, 0.5, 0], 'green', 'blue'});

    % Find transitions in mode and plot vertical lines accordingly
    start_idx = 1;
    for i = 2:length(mode_values)
        if mode_values(i) ~= mode_values(i-1) || i == length(mode_values)
            % Check if the mode value exists in mode_colors
            if isKey(mode_colors, mode_values(start_idx))
                mode_color = mode_colors(mode_values(start_idx));

                % Loop over each axis in ax_array
                for j = 1:length(ax_array)
                    % Draw a thick dashed vertical line at the start of the region
                    line(ax_array{j}, [mode_timestamps(start_idx), mode_timestamps(start_idx)], ...
                        y_limits{j}, 'LineStyle', '--', 'LineWidth', 1.2, 'Color', mode_color);
                end
            end

            % Update the start index for the next segment
            start_idx = i;
        end
    end
end