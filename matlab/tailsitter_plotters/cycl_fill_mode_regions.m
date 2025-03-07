%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
% chat gpt generated code use/modify with caution! %
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

function cycl_fill_mode_regions(mode_values, mode_timestamps, ax_array)

    % Get the y-limits for each axis in ax_array
    y_limits = cell(1, length(ax_array));
    for i = 1:length(ax_array)
        y_limits{i} = ylim(ax_array{i});
    end

    % Define colors for each mode
    mode_colors = containers.Map({-9600, 75, 9600}, {'orange', 'green', 'blue'});

    % Find transitions in mode and plot shaded regions accordingly
    start_idx = 1;
    for i = 2:length(mode_values)
        if mode_values(i) ~= mode_values(i-1) || i == length(mode_values)
            % Check if the mode value exists in mode_colors
            if isKey(mode_colors, mode_values(start_idx))
                mode_color = mode_colors(mode_values(start_idx));

                % Loop over each axis in ax_array
                for j = 1:length(ax_array)
                    % Plot shaded region for the current mode on each axis
                    h_fill = fill([mode_timestamps(start_idx), mode_timestamps(i-1), mode_timestamps(i-1), mode_timestamps(start_idx)], ...
                        [y_limits{j}(1), y_limits{j}(1), y_limits{j}(2), y_limits{j}(2)], ...
                        mode_color, 'FaceAlpha', 0.15, 'EdgeColor', 'none', 'Parent', ax_array{j});

                    % Send fill to the background
                    uistack(h_fill, 'bottom');
                end
            end

            % Update the start index for the next segment
            start_idx = i;
        end
    end
end