# CraftBeerPi4 Plugin Example to replace the native recipe controller 

- The plugin is not required, if you don't want to change anything to the native recipe creation capabilities of cbpi4

## This plugin is an example on how to create recipes. It basically has the same functionality as the included recipe creation controller but can be modified by the user requirements.

- Beer.xml file , MUMM JSON recipe file or kbh database needs to be uploaded via user interface
- BF recipe can be accessed directly via user interface (paid BF account required)
- If you want to use this plugin instead of the native recipe creation capabilities, you need to change the settings parameter 'RECIPE_CREATION_PATH' to 'creation'
- No other changes are required.
- This allows the user to modify the plugin to his needs and to combine steps as required for his equipment. 
- No change of cbpi4 is required


## Changelog:

- 22.02.25: (1.0.1) Update requirement for Cooldown step name to allow alternative cooldown steps. Name must contain 'Cooldown'
- 14.02.25: (1.0.0) Demo Version that can be used for recipe individualization as plugin -> Code modifications required
- 12.07.21: New version that can be used with the native upload capabilities of my fork
- 24.03.21:	Initial release
